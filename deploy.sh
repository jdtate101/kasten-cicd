#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Kasten CI/CD — Build & Deploy
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REGISTRY="harbor.apps.openshift2.lab.home"
IMAGE="${REGISTRY}/homelab/kasten-cicd"
TAG="${1:-latest}"
NAMESPACE="kasten-cicd"

BOLD_RED="\033[1;31m"
BOLD_GRN="\033[1;32m"
BOLD_YLW="\033[1;33m"
BOLD_WHT="\033[1;37m"
RESET="\033[0m"

log()  { echo -e "${BOLD_WHT}[$(date +%T)]${RESET} $*"; }
ok()   { echo -e "${BOLD_GRN}[$(date +%T)] ✓${RESET} $*"; }
warn() { echo -e "${BOLD_YLW}[$(date +%T)] ⚠${RESET} $*"; }
err()  { echo -e "${BOLD_RED}[$(date +%T)] ✗${RESET} $*"; exit 1; }

echo ""
echo -e "${BOLD_WHT}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${BOLD_WHT}  KASTEN CI/CD — BUILD & DEPLOY                   ${RESET}"
echo -e "${BOLD_WHT}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo ""

# ── Namespace ─────────────────────────────────────────────────────────────────
log "Ensuring namespace ${NAMESPACE} exists..."
oc get namespace "${NAMESPACE}" &>/dev/null || oc create namespace "${NAMESPACE}"
ok "Namespace ready"

# ── Build ─────────────────────────────────────────────────────────────────────
log "Building image ${IMAGE}:${TAG}..."
docker build -t "${IMAGE}:${TAG}" .
ok "Image built"

# ── Push ──────────────────────────────────────────────────────────────────────
log "Pushing to Harbor..."
docker push "${IMAGE}:${TAG}"
ok "Image pushed: ${IMAGE}:${TAG}"

# ── Deploy ────────────────────────────────────────────────────────────────────
log "Applying manifests..."
oc apply -f k8s/manifests.yaml
ok "Manifests applied"

# ── Restart to pick up latest image ──────────────────────────────────────────
log "Rolling restart..."
oc rollout restart deployment/kasten-cicd -n "${NAMESPACE}"
oc rollout status deployment/kasten-cicd -n "${NAMESPACE}" --timeout=120s
ok "Deployment rolled out"

# ── Route ─────────────────────────────────────────────────────────────────────
ROUTE=$(oc get route kasten-cicd -n "${NAMESPACE}" -o jsonpath='{.spec.host}' 2>/dev/null || echo "not found")
echo ""
echo -e "${BOLD_GRN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${BOLD_GRN}  DEPLOYED SUCCESSFULLY${RESET}"
echo -e "${BOLD_GRN}  URL: https://${ROUTE}${RESET}"
echo -e "${BOLD_GRN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo ""
