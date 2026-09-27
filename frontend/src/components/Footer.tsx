import ecoIcon from "../assets/eco-icon.png"
import { formatFetchedAt } from "../lib/format"

export default function Footer({ fetchedAtISO }: { fetchedAtISO?: string }) {
  return (
    <footer className="k-footer">
      <span className="k-footer__brand">
        <img className="eco-mark" src={ecoIcon} alt="" width={24} height={24} /> eco-app
      </span>
      <span>
        Unofficial fan project. Eco is a trademark of{" "}
        <a href="https://strangeloopgames.com/">Strange Loop Games</a>.
      </span>
      {fetchedAtISO && <span className="footer-stamp">snapshot {formatFetchedAt(fetchedAtISO)}</span>}
    </footer>
  )
}
