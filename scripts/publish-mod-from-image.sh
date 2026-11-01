#!/usr/bin/env bash
# Publishes the mod ZIPs baked into a built app image as GitHub Release assets, so the
# release bytes are the exact bytes the image shipped. Run after publish-image.sh.
set -euo pipefail

registry="ghcr.io"
image_name="coilyco/eco-app"

for required in GITHUB_TOKEN GITHUB_REPOSITORY; do
  if [ -z "${!required:-}" ]; then
    echo "${required} is required for the trusted mod-package lane." >&2
    exit 1
  fi
done

sha="${GITHUB_SHA:-$(git rev-parse HEAD)}"
case "${sha}" in
  *[!0-9a-f]*|"")
    echo "eco-app source sha is not a lowercase hexadecimal commit id." >&2
    exit 1
    ;;
esac
if [ "${#sha}" -ne 40 ]; then
  echo "eco-app source sha must be a full 40-character commit id." >&2
  exit 1
fi

image="${registry}/${image_name}:${sha}"
docker_config="$(mktemp -d)"
package_dir="$(mktemp -d)"
image_container_id=""
cleanup() {
  if [ -n "${image_container_id}" ]; then
    docker rm -f "${image_container_id}" >/dev/null 2>&1 || true
  fi
  rm -rf "${docker_config}" "${package_dir}"
}
trap cleanup EXIT
chmod 700 "${docker_config}" "${package_dir}"
export DOCKER_CONFIG="${docker_config}"

printf '%s' "${GITHUB_TOKEN}" \
  | docker login "${registry}" --username "${GITHUB_ACTOR:-coilyco-ops}" --password-stdin
docker pull "${image}"

image_container_id="$(docker create "${image}")"
docker cp "${image_container_id}:/mod-packages/." "${package_dir}/"
docker rm -f "${image_container_id}" >/dev/null
image_container_id=""

GITHUB_SHA="${sha}" MOD_PACKAGE_DIR="${package_dir}" python3 scripts/mod_packages.py publish
