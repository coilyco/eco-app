"""Register one typed operation for both REST and MCP transports."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, TypeVar, cast

from mcp.types import CallToolResult, TextContent, Tool, ToolAnnotations
from pydantic import BaseModel, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

InputModel = TypeVar("InputModel", bound=BaseModel)
OutputModel = TypeVar("OutputModel", bound=BaseModel)
RestMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DualRouteResult[PayloadModel: BaseModel]:
    """The shared human-readable and structured result of a dual route."""

    text: str
    payload: PayloadModel
    is_error: bool = False
    rest_status: int = 200


DualRouteHandler = Callable[[InputModel], Awaitable[DualRouteResult[OutputModel]]]


@dataclass(frozen=True)
class _RegisteredRoute:
    name: str
    title: str
    description: str
    rest_path: str
    rest_method: RestMethod
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    handler: Callable[[BaseModel], Awaitable[DualRouteResult[BaseModel]]]
    annotations: ToolAnnotations | None


class DualRouteRegistry:
    """Own route metadata and expose each registration through REST and MCP."""

    def __init__(self) -> None:
        self._routes: dict[str, _RegisteredRoute] = {}
        self._rest_keys: set[tuple[str, RestMethod]] = set()

    def register(
        self,
        *,
        name: str,
        title: str,
        description: str,
        rest_path: str,
        rest_method: RestMethod,
        input_model: type[InputModel],
        output_model: type[OutputModel],
        annotations: ToolAnnotations | None = None,
    ) -> Callable[
        [DualRouteHandler[InputModel, OutputModel]],
        DualRouteHandler[InputModel, OutputModel],
    ]:
        """Wrap one handler with its shared REST and MCP contract."""
        if not rest_path.startswith("/"):
            raise ValueError("REST paths must start with '/'")
        if name in self._routes:
            raise ValueError(f"duplicate MCP tool name: {name}")
        rest_key = (rest_path, rest_method)
        if rest_key in self._rest_keys:
            raise ValueError(f"duplicate REST route: {rest_method} {rest_path}")

        def decorator(
            handler: DualRouteHandler[InputModel, OutputModel],
        ) -> DualRouteHandler[InputModel, OutputModel]:
            registered = _RegisteredRoute(
                name=name,
                title=title,
                description=description,
                rest_path=rest_path,
                rest_method=rest_method,
                input_model=input_model,
                output_model=output_model,
                handler=cast(
                    Callable[[BaseModel], Awaitable[DualRouteResult[BaseModel]]],
                    handler,
                ),
                annotations=annotations,
            )
            self._routes[name] = registered
            self._rest_keys.add(rest_key)
            return handler

        return decorator

    def has_tool(self, name: str) -> bool:
        """Return whether this registry owns an MCP tool name."""
        return name in self._routes

    def rest_path(self, name: str) -> str | None:
        """Return the REST path a registered GET tool is served at, else None."""
        route = self._routes.get(name)
        return route.rest_path if route and route.rest_method == "GET" else None

    def mcp_tools(self) -> list[Tool]:
        """Build MCP discovery entries from the registered typed models."""
        return [
            Tool(
                name=route.name,
                title=route.title,
                description=route.description,
                inputSchema=route.input_model.model_json_schema(by_alias=True),
                outputSchema=route.output_model.model_json_schema(by_alias=True),
                annotations=route.annotations,
            )
            for route in self._routes.values()
        ]

    async def call_mcp(self, name: str, arguments: dict[str, Any]) -> CallToolResult:
        """Validate and invoke a registered operation as an MCP tool."""
        route = self._routes[name]
        try:
            request_model = route.input_model.model_validate(arguments)
        except ValidationError as error:
            return self._mcp_validation_error(error)

        try:
            result = await route.handler(request_model)
            payload_model = route.output_model.model_validate(result.payload)
            payload = payload_model.model_dump(mode="json", by_alias=True)
        except Exception:
            logger.exception("Dual MCP route %s failed", route.name)
            return self._mcp_internal_error()
        encoded = json.dumps(payload, separators=(",", ":"))
        return CallToolResult(
            content=[
                TextContent(type="text", text=result.text),
                TextContent(type="text", text=encoded),
            ],
            structuredContent=payload,
            isError=result.is_error,
        )

    def starlette_routes(self) -> list[Route]:
        """Build Starlette routes that invoke the same registered handlers."""
        return [
            Route(
                route.rest_path,
                self._rest_endpoint(route),
                methods=[route.rest_method],
                name=f"dual:{route.name}",
            )
            for route in self._routes.values()
        ]

    def _rest_endpoint(
        self, route: _RegisteredRoute
    ) -> Callable[[Request], Awaitable[JSONResponse]]:
        async def endpoint(request: Request) -> JSONResponse:
            try:
                arguments = await self._rest_arguments(request, route.rest_method)
            except ValueError as error:
                return JSONResponse(
                    {"error": "invalid_json_body", "message": str(error)},
                    status_code=400,
                )

            try:
                request_model = route.input_model.model_validate(arguments)
            except ValidationError as error:
                return JSONResponse(
                    {
                        "error": "invalid_arguments",
                        "details": self._validation_details(error),
                    },
                    status_code=422,
                )

            try:
                result = await route.handler(request_model)
                payload_model = route.output_model.model_validate(result.payload)
            except Exception:
                logger.exception("Dual REST route %s failed", route.name)
                return JSONResponse(
                    {
                        "error": "operation_failed",
                        "message": "The operation could not be completed.",
                    },
                    status_code=500,
                )
            return JSONResponse(
                payload_model.model_dump(mode="json", by_alias=True),
                status_code=result.rest_status,
            )

        return endpoint

    @staticmethod
    async def _rest_arguments(request: Request, method: RestMethod) -> dict[str, Any]:
        arguments: dict[str, Any] = dict(request.query_params)
        if method in {"POST", "PUT", "PATCH"}:
            try:
                body = await request.json()
            except (TypeError, ValueError) as error:
                raise ValueError("request body must be valid JSON") from error
            if not isinstance(body, dict):
                raise ValueError("request body must be a JSON object")
            arguments.update(body)
        arguments.update(request.path_params)
        return arguments

    @classmethod
    def _mcp_validation_error(cls, error: ValidationError) -> CallToolResult:
        details = cls._validation_details(error)
        encoded = json.dumps({"error": "invalid_arguments", "details": details})
        return CallToolResult(
            content=[
                TextContent(type="text", text="Invalid arguments for this tool."),
                TextContent(type="text", text=encoded),
            ],
            isError=True,
        )

    @staticmethod
    def _mcp_internal_error() -> CallToolResult:
        payload = {
            "error": "operation_failed",
            "message": "The operation could not be completed.",
        }
        return CallToolResult(
            content=[
                TextContent(type="text", text=payload["message"]),
                TextContent(type="text", text=json.dumps(payload)),
            ],
            isError=True,
        )

    @staticmethod
    def _validation_details(error: ValidationError) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]],
            error.errors(include_url=False, include_context=False, include_input=False),
        )
