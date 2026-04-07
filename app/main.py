import os
import ssl
import json
import urllib.request
import urllib.error
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Kasten CI/CD Trigger")

# ── In-cluster Kubernetes API config ─────────────────────────────────────────
KUBE_API   = "https://kubernetes.default.svc"
SA_TOKEN   = "/var/run/secrets/kubernetes.io/serviceaccount/token"
SA_CACERT  = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
K10_NAMESPACE = os.environ.get("K10_NAMESPACE", "kasten-io")

def get_token() -> str:
    try:
        with open(SA_TOKEN) as f:
            return f.read().strip()
    except Exception as e:
        logger.error(f"Cannot read SA token: {e}")
        return ""

def get_ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.load_verify_locations(SA_CACERT)
    return ctx

def kube_get(path: str) -> dict:
    url = f"{KUBE_API}{path}"
    logger.info(f"KUBE GET {url}")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {get_token()}"})
    try:
        with urllib.request.urlopen(req, context=get_ssl_ctx(), timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise Exception(f"HTTP {e.code} from kube API: {body[:500]}")

def kube_post(path: str, payload: dict) -> dict:
    url = f"{KUBE_API}{path}"
    logger.info(f"KUBE POST {url}")
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {get_token()}",
            "Content-Type": "application/json",
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, context=get_ssl_ctx(), timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise Exception(f"HTTP {e.code} from kube API: {body[:500]}")

def kube_delete(path: str) -> None:
    url = f"{KUBE_API}{path}"
    logger.info(f"KUBE DELETE {url}")
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {get_token()}"},
        method="DELETE"
    )
    try:
        with urllib.request.urlopen(req, context=get_ssl_ctx(), timeout=30) as resp:
            return
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise Exception(f"HTTP {e.code} from kube API: {body[:500]}")

# ── Models ────────────────────────────────────────────────────────────────────
class RunActionRequest(BaseModel):
    policy_name: str
    policy_namespace: str

class ExportActionRequest(BaseModel):
    restore_point_name: str
    restore_point_namespace: str
    restore_point_time: str        # scheduledTime from RestorePoint status.actionTime
    app_name: str                  # k10.kasten.io/appName label
    app_namespace: str             # k10.kasten.io/appNamespace label
    policy_name: str
    policy_namespace: str
    location_profile_name: str = ""
    location_profile_namespace: str = ""


# ── Helpers ───────────────────────────────────────────────────────────────────
def is_on_demand(policy: dict) -> bool:
    spec = policy.get("spec", {})
    frequency = spec.get("frequency", None)
    if frequency and frequency not in ("", "@onDemand"):
        return False
    preset = spec.get("presetRef", {})
    preset_name = preset.get("name", "")
    scheduled_keywords = ["daily", "weekly", "monthly", "hourly", "schedule", "frequent"]
    if any(kw in preset_name.lower() for kw in scheduled_keywords):
        return False
    return True

def extract_policy_info(policy: dict) -> dict:
    meta = policy.get("metadata", {})
    spec = policy.get("spec", {})
    status = policy.get("status", {})
    return {
        "name": meta.get("name"),
        "namespace": meta.get("namespace"),
        "comment": spec.get("comment", ""),
        "actions": spec.get("actions", []),
        "selector": spec.get("selector", {}),
        "presetRef": spec.get("presetRef", {}),
        "lastRunTime": status.get("lastRunTime"),
        "lastRunStatus": status.get("lastRunStatus"),
        "frequency": spec.get("frequency", "@onDemand"),
    }

# ── API Routes ────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    token = get_token()
    return {
        "status": "ok",
        "kube_api": KUBE_API,
        "k10_namespace": K10_NAMESPACE,
        "has_token": bool(token),
    }

@app.get("/api/policies")
async def get_policies():
    try:
        data = kube_get("/apis/config.kio.kasten.io/v1alpha1/policies")
        items = data.get("items", [])
        logger.info(f"Total policies: {len(items)}")
        if items:
            logger.info(f"Sample policy spec keys: {list(items[0].get('spec',{}).keys())}")
        on_demand = [extract_policy_info(p) for p in items if is_on_demand(p)]
        logger.info(f"On-demand: {len(on_demand)}")
        return {"policies": on_demand}
    except Exception as e:
        logger.error(f"Error fetching policies: {e}")
        raise HTTPException(status_code=502, detail=str(e))

@app.get("/api/policies/{namespace}/{name}/restorepoints")
async def get_restore_points(namespace: str, name: str):
    try:
        # RestorePoints live in the APP namespace, not kasten-io — query cluster-wide
        data = kube_get(
            f"/apis/apps.kio.kasten.io/v1alpha1/restorepoints"
            f"?labelSelector=k10.kasten.io%2FpolicyName%3D{name}"
        )
        items = data.get("items", [])
        restore_points = []
        for item in items:
            meta = item.get("metadata", {})
            labels = meta.get("labels", {})
            # Exclude export restore points — they have an exportProfile label
            if "k10.kasten.io/exportProfile" in labels:
                continue
            status = item.get("status", {})
            restore_points.append({
                "id": meta.get("name"),
                "namespace": meta.get("namespace"),
                "createdAt": status.get("actionTime") or meta.get("creationTimestamp"),
                "scheduledTime": status.get("scheduledTime") or status.get("actionTime") or meta.get("creationTimestamp"),
                "policyName": labels.get("k10.kasten.io/policyName", name),
                "appName": labels.get("k10.kasten.io/appName", ""),
                "appNamespace": labels.get("k10.kasten.io/appNamespace", ""),
                "runActionName": labels.get("k10.kasten.io/runActionName", ""),
            })
        restore_points.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        return {"restorePoints": restore_points}
    except Exception as e:
        logger.error(f"Error fetching restore points: {e}")
        raise HTTPException(status_code=502, detail=str(e))

@app.get("/api/profiles")
async def get_location_profiles():
    try:
        data = kube_get(f"/apis/config.kio.kasten.io/v1alpha1/namespaces/{K10_NAMESPACE}/profiles")
        items = data.get("items", [])
        profiles = []
        for item in items:
            meta = item.get("metadata", {})
            spec = item.get("spec", {})
            loc = spec.get("locationSpec", {})
            profiles.append({
                "name": meta.get("name"),
                "namespace": meta.get("namespace"),
                "type": loc.get("type", "unknown"),
                "objectStore": loc.get("objectStore", {}),
            })
        return {"profiles": profiles}
    except Exception as e:
        logger.error(f"Error fetching profiles: {e}")
        raise HTTPException(status_code=502, detail=str(e))

@app.post("/api/run")
async def trigger_run_action(req: RunActionRequest):
    try:
        payload = {
            "apiVersion": "actions.kio.kasten.io/v1alpha1",
            "kind": "RunAction",
            "metadata": {
                "generateName": f"cicd-run-{req.policy_name}-",
                "namespace": req.policy_namespace,
            },
            "spec": {
                "subject": {
                    "apiVersion": "config.kio.kasten.io/v1alpha1",
                    "kind": "Policy",
                    "name": req.policy_name,
                    "namespace": req.policy_namespace,
                }
            }
        }
        result = kube_post(
            f"/apis/actions.kio.kasten.io/v1alpha1/namespaces/{req.policy_namespace}/runactions",
            payload
        )
        action_name = result.get("metadata", {}).get("name", "unknown")
        logger.info(f"Triggered run action {action_name} for policy {req.policy_name}")
        return {"actionName": action_name, "namespace": req.policy_namespace, "status": "triggered"}
    except Exception as e:
        logger.error(f"Error triggering run action: {e}")
        raise HTTPException(status_code=502, detail=str(e))

@app.post("/api/export")
async def trigger_export_action(req: ExportActionRequest):
    try:
        # Fetch profile from policy if not overridden
        if req.location_profile_name:
            profile = {"name": req.location_profile_name, "namespace": req.location_profile_namespace}
        else:
            policy_data = kube_get(
                f"/apis/config.kio.kasten.io/v1alpha1/namespaces/{req.policy_namespace}/policies/{req.policy_name}"
            )
            export_params = {}
            for action in policy_data.get("spec", {}).get("actions", []):
                if action.get("action") == "export":
                    export_params = action.get("exportParameters", {})
                    break
            profile = export_params.get("profile", {})
            if not profile:
                raise Exception("No export profile found in policy and none specified")

        # Minimal spec mirroring what K10 UI creates — omit migrationToken/receiveString
        # so K10 generates them fresh. Subject is the RestorePoint with full apiVersion/kind.
        payload = {
            "apiVersion": "actions.kio.kasten.io/v1alpha1",
            "kind": "ExportAction",
            "metadata": {
                "generateName": "cicd-export-",
                "namespace": req.restore_point_namespace,
            },
            "spec": {
                "subject": {
                    "apiVersion": "apps.kio.kasten.io/v1alpha1",
                    "kind": "RestorePoint",
                    "name": req.restore_point_name,
                    "namespace": req.restore_point_namespace,
                },
                "exportData": {"enabled": True},
                "profile": profile,
            }
        }
        logger.info(f"ExportAction payload: {payload}")
        result = kube_post(
            f"/apis/actions.kio.kasten.io/v1alpha1/namespaces/{req.app_namespace}/exportactions",
            payload
        )
        action_name = result.get("metadata", {}).get("name", "unknown")
        logger.info(f"Triggered export action {action_name} in {req.app_namespace}")
        return {"actionName": action_name, "namespace": req.app_namespace, "status": "triggered"}
    except Exception as e:
        logger.error(f"Error triggering export action: {e}")
        raise HTTPException(status_code=502, detail=str(e))

@app.get("/api/actions/{namespace}/{name}")
async def get_action_status(namespace: str, name: str):
    for kind in ["runactions", "exportactions"]:
        try:
            data = kube_get(
                f"/apis/actions.kio.kasten.io/v1alpha1/namespaces/{namespace}/{kind}/{name}"
            )
            status = data.get("status", {})
            return {
                "name": name,
                "kind": kind,
                "state": status.get("state", "Running"),
                "progress": status.get("progress", 0),
                "error": status.get("error"),
            }
        except:
            continue
    raise HTTPException(status_code=404, detail="Action not found")

# ── Static frontend ───────────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory="/app/static"), name="static")

@app.get("/")
async def root():
    return FileResponse("/app/static/index.html")


class RetireRequest(BaseModel):
    restore_point_name: str
    restore_point_namespace: str


@app.get("/api/policies/{namespace}/{name}/allrestorepoints")
async def get_all_restore_points(namespace: str, name: str):
    """Return ALL restore points for a policy (snapshots + exports), flagging which have no expiry."""
    try:
        data = kube_get(
            f"/apis/apps.kio.kasten.io/v1alpha1/restorepoints"
            f"?labelSelector=k10.kasten.io%2FpolicyName%3D{name}"
        )
        items = data.get("items", [])
        restore_points = []
        for item in items:
            meta = item.get("metadata", {})
            labels = meta.get("labels", {})
            status = item.get("status", {})
            spec = item.get("spec", {})
            # Check for expiry annotation
            annotations = meta.get("annotations", {})
            expiry = annotations.get("k10.kasten.io/expiresAt") or spec.get("expiresAt")
            is_export = "k10.kasten.io/exportProfile" in labels
            restore_points.append({
                "id": meta.get("name"),
                "namespace": meta.get("namespace"),
                "createdAt": status.get("actionTime") or meta.get("creationTimestamp"),
                "policyName": labels.get("k10.kasten.io/policyName", name),
                "appName": labels.get("k10.kasten.io/appName", ""),
                "appNamespace": labels.get("k10.kasten.io/appNamespace", ""),
                "exportProfile": labels.get("k10.kasten.io/exportProfile", ""),
                "isExport": is_export,
                "expiresAt": expiry,
                "hasExpiry": bool(expiry),
            })
        restore_points.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        return {"restorePoints": restore_points}
    except Exception as e:
        logger.error(f"Error fetching all restore points: {e}")
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/api/retire")
async def retire_restore_point(req: RetireRequest):
    """Delete RestorePoint directly — K10 GC handles export location cleanup."""
    try:
        kube_delete(
            f"/apis/apps.kio.kasten.io/v1alpha1/namespaces/{req.restore_point_namespace}/restorepoints/{req.restore_point_name}"
        )
        logger.info(f"Deleted restore point {req.restore_point_name} in {req.restore_point_namespace}")
        return {"actionName": f"delete-{req.restore_point_name}", "status": "deleted"}
    except Exception as e:
        logger.error(f"Error deleting restore point: {e}")
        raise HTTPException(status_code=502, detail=str(e))
