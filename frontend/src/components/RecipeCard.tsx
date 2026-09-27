import { Link } from "react-router-dom"
import ItemLink from "./ItemLink"
import type { Recipe, RecipeComponent, RecipeIndex } from "../lib/recipesApi"
import { recipeAnchor } from "../lib/itemPage"
import { formatCount, formatDuration, prettifyEcoName } from "../lib/format"

// One recipe, as a card on its product's item page (eco-app#8383). The id is
// the anchor /recipe?id= redirects to, so a deep link lands on this card.

function craftTime(minutes: number): string {
  if (!minutes) return "—"
  return formatDuration(minutes * 60)
}

export function skillLabel(skillName: string, index: RecipeIndex): string {
  const def = index.skills.find((s) => s.name === skillName)
  if (def) return def.displayName
  return prettifyEcoName(skillName.replace(/Skill$/, ""))
}

// Items link to their own page. A tag is not one item, so it keeps the
// directory's reverse lookup instead.
function ComponentRow({ c }: { c: RecipeComponent }) {
  return (
    <li data-testid="recipe-component">
      <span className="recipe-qty">{formatCount(c.quantity)}×</span>{" "}
      {c.isTag ? (
        <span>
          {c.displayName} <span className="section-sub">(any kind)</span>{" "}
          <Link
            className="recipe-sublink"
            to={`/recipes?ingredient=${encodeURIComponent(c.item)}`}
            data-testid="recipe-uses-link"
          >
            which ones →
          </Link>
        </span>
      ) : (
        <ItemLink className="linklike" item={c.item}>
          {c.displayName}
        </ItemLink>
      )}
    </li>
  )
}

export default function RecipeCard({
  recipe,
  index,
  main = false,
}: {
  recipe: Recipe
  index: RecipeIndex
  main?: boolean
}) {
  const titleId = `${recipeAnchor(recipe.name)}-title`
  return (
    <article
      className="recipe-card"
      id={recipeAnchor(recipe.name)}
      tabIndex={-1}
      aria-labelledby={titleId}
      data-testid="recipe-card"
    >
      <div className="recipe-card-head">
        <h3 className="recipe-card-title" id={titleId}>
          {recipe.displayName}
        </h3>
        {main && <span className="recipe-card-tag">main recipe</span>}
      </div>
      <div className="recipe-facts" data-testid="recipe-facts">
        <div className="fact">
          <span className="fact-label">Made at</span>
          <span className="fact-value">
            {recipe.station ? (
              <Link className="linklike" to={`/recipes?station=${encodeURIComponent(recipe.station)}`}>
                {prettifyEcoName(recipe.station)}
              </Link>
            ) : (
              "By hand"
            )}
          </span>
        </div>
        <div className="fact">
          <span className="fact-label">Skill</span>
          <span className="fact-value">
            {recipe.skill ? (
              <Link className="linklike" to={`/recipes?skill=${encodeURIComponent(recipe.skill.name)}`}>
                {skillLabel(recipe.skill.name, index)}
                {recipe.skill.level > 0 ? ` · level ${recipe.skill.level}` : ""}
              </Link>
            ) : (
              "None"
            )}
          </span>
        </div>
        <div className="fact">
          <span className="fact-label">Calories</span>
          <span className="fact-value">{recipe.laborCost ? `${formatCount(recipe.laborCost)} cal` : "—"}</span>
        </div>
        <div className="fact">
          <span className="fact-label">Craft time</span>
          <span className="fact-value">{craftTime(recipe.craftMinutes)}</span>
        </div>
        {recipe.tableTierRequired != null && (
          <div className="fact">
            <span className="fact-label">Table tier</span>
            <span className="fact-value">Tier {recipe.tableTierRequired}</span>
          </div>
        )}
      </div>
      <div className="recipe-bom" data-testid="recipe-bom">
        <div>
          <h4 className="section-title-sm">Ingredients</h4>
          {recipe.ingredients.length === 0 ? (
            <p className="empty-note">No ingredients needed.</p>
          ) : (
            <ul className="recipe-list" data-testid="recipe-ingredients">
              {recipe.ingredients.map((c) => (
                <ComponentRow key={`${c.item}-${c.isTag}`} c={c} />
              ))}
            </ul>
          )}
        </div>
        <div>
          <h4 className="section-title-sm">Makes</h4>
          <ul className="recipe-list" data-testid="recipe-products">
            <ComponentRow c={recipe.product} />
            {recipe.byproducts.map((c) => (
              <ComponentRow key={`${c.item}-${c.isTag}`} c={c} />
            ))}
          </ul>
        </div>
      </div>
    </article>
  )
}
