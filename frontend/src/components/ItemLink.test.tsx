import { render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { describe, expect, it } from "vitest"
import ItemLink, { itemHref } from "./ItemLink"

describe("ItemLink", () => {
  it("builds an encoded item-pivot target", () => {
    render(
      <MemoryRouter>
        <ItemLink item="Fancy Item/One & Two">Fancy item</ItemLink>
      </MemoryRouter>,
    )

    expect(screen.getByRole("link", { name: "Fancy item" })).toHaveAttribute(
      "href",
      "/item?id=Fancy%20Item%2FOne%20%26%20Two",
    )
    expect(itemHref("A+B")).toBe("/item?id=A%2BB")
    // The page key drops the Item suffix, so a recipe name and its product agree (eco-app#8383).
    expect(itemHref("AcornPowderItem")).toBe("/item?id=AcornPowder")
  })

  it("keeps content plain when there is no concrete item id", () => {
    render(
      <MemoryRouter>
        <ItemLink item="">Any wood</ItemLink>
      </MemoryRouter>,
    )

    expect(screen.getByText("Any wood")).not.toHaveAttribute("href")
    expect(screen.queryByRole("link", { name: "Any wood" })).toBeNull()
  })
})
