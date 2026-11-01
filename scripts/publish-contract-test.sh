#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
bash -n \
  "${repo_root}/scripts/publish-image.sh" \
  "${repo_root}/scripts/publish-mod-from-image.sh"

test_dir="$(mktemp -d)"
cleanup() {
  rm -rf "${test_dir}"
}
trap cleanup EXIT

export FAKE_DOCKER_LOG="${test_dir}/docker.log"
export FAKE_GH_LOG="${test_dir}/gh.log"

docker() {
  local command="${1:-}"
  shift || true

  printf '%s' "${command}" >>"${FAKE_DOCKER_LOG}"
  for argument in "$@"; do
    printf '\t%s' "${argument}" >>"${FAKE_DOCKER_LOG}"
  done
  printf '\n' >>"${FAKE_DOCKER_LOG}"

  case "${command}" in
    login)
      cat >/dev/null
      ;;
    create)
      printf 'image-container\n'
      ;;
    cp)
      # docker cp <container>:/mod-packages/. <dir>/ : lay down one package.
      local destination="${2%/}"
      printf 'zip' >"${destination}/eco-replay-1.0.0.zip"
      printf '{}' >"${destination}/eco-replay-1.0.0.json"
      printf 'sum' >"${destination}/eco-replay-1.0.0.zip.sha256"
      printf '%s' '{"schema":1,"packages":[{"package_name":"eco-replay","archive":"eco-replay-1.0.0.zip","metadata":"eco-replay-1.0.0.json","checksum_file":"eco-replay-1.0.0.zip.sha256","registry_version":"1.0.0+aaaa"}]}' >"${destination}/manifest.json"
      ;;
    pull | rm)
      ;;
    *)
      printf 'unexpected docker command in publisher contract test: %s\n' "${command}" >&2
      return 1
      ;;
  esac
}
export -f docker

mkdir -p "${test_dir}/bin"
cat >"${test_dir}/bin/gh" <<'GH'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"${FAKE_GH_LOG}"
case "$1 $2" in
  "release view")
    if [[ " $* " == *" --json "* ]]; then
      printf '{"assets":[]}\n'
    elif ! grep -q '^release create' "${FAKE_GH_LOG}"; then
      exit 1
    fi
    ;;
esac
GH
chmod +x "${test_dir}/bin/gh"

(
  cd "${repo_root}"
  PATH="${test_dir}/bin:${PATH}" \
    GITHUB_TOKEN="test-token" \
    GITHUB_REPOSITORY="coilyco/eco-app" \
    GITHUB_SHA="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" \
    bash scripts/publish-mod-from-image.sh
)

require_log_entry() {
  local log="$1"
  local expected="$2"
  if ! grep -F -q -- "${expected}" "${log}"; then
    printf 'publisher contract is missing operation: %s\n' "${expected}" >&2
    return 1
  fi
}

require_log_entry "${FAKE_DOCKER_LOG}" $'pull\tghcr.io/coilyco/eco-app:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
require_log_entry "${FAKE_DOCKER_LOG}" "image-container:/mod-packages/."
require_log_entry "${FAKE_GH_LOG}" "release create mods-aaaaaaaaaaaa --repo coilyco/eco-app --target aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
for asset in eco-replay-1.0.0.zip eco-replay-1.0.0.json eco-replay-1.0.0.zip.sha256; do
  if ! grep -E -q "^release upload mods-aaaaaaaaaaaa .*${asset} --repo coilyco/eco-app$" "${FAKE_GH_LOG}"; then
    printf 'publisher contract never uploaded %s\n' "${asset}" >&2
    exit 1
  fi
done

if grep -F -q -- "--volume" "${FAKE_DOCKER_LOG}"; then
  printf 'publisher contract must not bind-mount runner-local paths\n' >&2
  exit 1
fi

printf 'publisher repository and credential contract passed\n'
