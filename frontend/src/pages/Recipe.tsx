import { Link, Navigate, useSearchParams } from "react-router-dom"
import FreshnessNote from "../components/FreshnessNote"
import Layout from "../components/Layout"
import { findRecipe, itemIdsFor, recipeHref } from "../lib/itemPage"
import { fetchRecipeIndex } from "../lib/recipesApi"
import { useFreshData } from "../lib/useFreshData"

// Recipes no longer have a page of their own (eco-app#8383). A recipe is a card
// on its primary product's item page, so /recipe?id=<name> resolves the name
// against the graph and redirects to /item?id=<product>#recipe-<name>. The
// route stays in the manifest so old links never 404.
export default function Recipe() {
  const [params] = useSearchParams()
  const id = params.get("id") ?? ""

  // Refresh contract lives in freshness.ts, not here (eco-app#201).
  const recipesPlane = useFreshData("recipes", fetchRecipeIndex)
  const index = recipesPlane.data

  const recipe = index && id ? findRecipe(index, id) : null
  if (recipe) return <Navigate to={recipeHref(recipe)} replace />
  // Some links carried an item's id here instead of a recipe's.
  if (index && id && itemIdsFor(index, id).length > 0) {
    return <Navigate to={`/item?id=${encodeURIComponent(id)}`} replace />
  }

  return (
    <Layout fetchedAtISO={index?.fetchedAtISO}>
      <section className="hero hero-compact">
        <p className="hero-kicker">
          <Link to="/recipes" className="linklike" data-testid="back-to-recipes">
            ← Recipe directory
          </Link>
        </p>
        <h1 className="hero-title">{id && !index && !recipesPlane.error ? "Finding that recipe…" : "Recipe"}</h1>
        {id && recipesPlane.error && (
          <p className="hero-pill hero-pill-muted" data-testid="recipe-error">
            Recipes can't load right now. Try again in a minute.
          </p>
        )}
        <FreshnessNote plane="recipes" loadedAt={recipesPlane.loadedAt} />
      </section>

      {!id && (
        <section>
          <p className="empty-note" data-testid="recipe-missing">
            No recipe picked. Choose one from the{" "}
            <Link className="linklike" to="/recipes">
              recipe directory
            </Link>
            .
          </p>
        </section>
      )}

      {id && index && (
        <section>
          <p className="empty-note" data-testid="recipe-not-found">
            There's no recipe called “{id}”. It may have been renamed. Search the{" "}
            <Link className="linklike" to="/recipes">
              recipe directory
            </Link>{" "}
            instead.
          </p>
        </section>
      )}
    </Layout>
  )
}
