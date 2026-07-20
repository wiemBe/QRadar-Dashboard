#!/usr/bin/env bash
# Load the QRadar SEC token and CA bundle into the `qradar_secrets` volume.
#
# Why not a bind mount of ./.secrets?
#   On an SELinux-enforcing host the repository is labelled user_home_t, which
#   no container process can read — not even root in the container. The usual
#   answer is a `:z`/`:Z` mount, but that relabels the host source tree as a
#   side effect. Streaming the files in over stdin touches no host label at all,
#   so SELinux stays enforcing and ./.secrets keeps its 0600/user_home_t state.
#
# Re-running is safe: it overwrites both files in place.
#
#   ./deploy/load-secrets.sh
set -euo pipefail

cd "$(dirname "$0")/.."

# Must match volumes.qradar_secrets.name in docker-compose.yml.
VOLUME=qradar-observability-secrets
TOKEN_SRC=.secrets/qradar.sec
CA_SRC=.secrets/qradar-ca.pem

# uid of `appuser` in backend/Dockerfile. The files are mounted read-only and
# must be readable by that account and by nobody else.
APP_UID=10001

for f in "$TOKEN_SRC" "$CA_SRC"; do
  [ -r "$f" ] || { echo "missing: $f" >&2; exit 1; }
done

docker volume create "$VOLUME" >/dev/null

# One container, both files over a tar stream on stdin. The token never appears
# in argv, so it stays out of `ps` output and the shell history.
tar -cf - -C .secrets qradar.sec qradar-ca.pem |
  docker run -i --rm -v "$VOLUME:/dst" alpine sh -c '
    set -e
    tar -xf - -C /dst
    mv /dst/qradar.sec     /dst/qradar_sec_token
    mv /dst/qradar-ca.pem  /dst/qradar_ca.pem
    chown '"$APP_UID"':'"$APP_UID"' /dst/qradar_sec_token /dst/qradar_ca.pem
    chmod 400 /dst/qradar_sec_token
    chmod 444 /dst/qradar_ca.pem
  '

echo "loaded into $VOLUME:"
docker run --rm -v "$VOLUME:/dst" alpine ls -l /dst
