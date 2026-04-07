# Kasten CI/CD Trigger

Manual on-demand trigger UI for Kasten K10 backup policies. Runs in-cluster on OpenShift, communicates directly with the Kubernetes API using a service account token — no K10 gateway auth required.

## Features

- **On-demand policy list** — fetches only policies with no schedule preset (filters out `daily`, `weekly`, `monthly`, `hourly` presets)
- **Run Action** — one-click manual run of any on-demand policy with confirmation dialog
- **Export Action** — select a snapshot restore point, choose a target location profile (or use the policy default), trigger an export — K10 generates the migration token and receive string automatically
- **Cleanup** — list all unexpired restore points for a policy (snapshots and/or exports), select individually or bulk, and delete them — K10 garbage collection handles export location cleanup on next GC run
- **Action log** — live status polling every 5s with progress bar per triggered action
- **Command output panel** — terminal-style YAML/command log of every action submitted this session

## Project Structure

```
kasten-cicd/
├── app/
│   ├── main.py          # FastAPI backend
│   └── static/
│       └── index.html   # Single-file frontend (vanilla JS)
├── k8s/
│   └── manifests.yaml   # SA, ClusterRole, ClusterRoleBinding, Deployment, Service, Route
├── Dockerfile           # Python 3.12 Alpine
├── deploy.sh            # Build + push + deploy script
└── README.md
```

## Deploy

```bash
chmod +x deploy.sh
./deploy.sh

# Or with a specific tag
./deploy.sh v1.2
```

The deploy script will:
1. Create the `kasten-cicd` namespace if missing
2. Build and push the image to Harbor (`harbor.apps.openshift2.lab.home/homelab/kasten-cicd`)
3. Apply all k8s manifests
4. Rolling-restart the deployment
5. Print the route URL

For manual builds:

```bash
docker build -t harbor.apps.openshift2.lab.home/homelab/kasten-cicd:latest . && \
docker push harbor.apps.openshift2.lab.home/homelab/kasten-cicd:latest && \
oc rollout restart deployment/kasten-cicd -n kasten-cicd
```

## Environment Variables

| Variable        | Default                              | Description                                    |
|-----------------|--------------------------------------|------------------------------------------------|
| `K10_NAMESPACE` | `kasten-io`                          | Namespace where K10 is installed               |
| `K10_TOKEN`     | *(empty)*                            | Override bearer token — defaults to SA token   |

The app does **not** use the K10 HTTP gateway. All API calls go directly to `https://kubernetes.default.svc` using the auto-mounted in-cluster service account token.

## Kubernetes API Calls

The backend uses the K10 CRD API groups directly:

| Method   | Path                                                                    | Purpose                          |
|----------|-------------------------------------------------------------------------|----------------------------------|
| `GET`    | `/apis/config.kio.kasten.io/v1alpha1/policies`                          | List all policies (filtered)     |
| `GET`    | `/apis/config.kio.kasten.io/v1alpha1/namespaces/kasten-io/profiles`     | List location profiles           |
| `GET`    | `/apis/config.kio.kasten.io/v1alpha1/namespaces/{ns}/policies/{name}`   | Fetch policy export params       |
| `GET`    | `/apis/apps.kio.kasten.io/v1alpha1/restorepoints?labelSelector=...`     | List restore points for policy   |
| `POST`   | `/apis/actions.kio.kasten.io/v1alpha1/namespaces/{ns}/runactions`       | Trigger run action               |
| `POST`   | `/apis/actions.kio.kasten.io/v1alpha1/namespaces/{ns}/exportactions`    | Trigger export action            |
| `GET`    | `/apis/actions.kio.kasten.io/v1alpha1/namespaces/{ns}/runactions/{n}`   | Poll action status               |
| `GET`    | `/apis/actions.kio.kasten.io/v1alpha1/namespaces/{ns}/exportactions/{n}`| Poll export action status        |
| `DELETE` | `/apis/apps.kio.kasten.io/v1alpha1/namespaces/{ns}/restorepoints/{n}`   | Delete restore point (cleanup)   |

### Key API behaviours discovered

**Policy filtering** — on-demand policies have no `spec.frequency` and no `spec.presetRef` pointing to a named schedule. The filter checks for absence of scheduling keywords (`daily`, `weekly`, `monthly`, `hourly`, `schedule`, `frequent`) in the preset name.

**Restore points** — live in the **app namespace** (e.g. `retro-game`), not `kasten-io`. Queried cluster-wide with a label selector on `k10.kasten.io/policyName`. Export restore points are identified by the presence of the `k10.kasten.io/exportProfile` label and excluded from the snapshot picker.

**ExportAction payload** — must reference the RestorePoint with full `apiVersion`/`kind` in the subject. Do **not** include `migrationToken`, `receiveString`, `frequency`, or `scheduledTime` — K10 generates these internally when the action is admitted. The action must be posted to the **app namespace**, not `kasten-io`.

**Cleanup** — K10 does not expose a usable `RetireAction` API endpoint in this version. Direct `DELETE` on the RestorePoint CRD works correctly and triggers K10's garbage collector to clean up the associated export location data on the next GC run.

## RBAC

The `kasten-cicd` ServiceAccount is granted a ClusterRole covering:

| API Group                    | Resources                              | Verbs                        |
|------------------------------|----------------------------------------|------------------------------|
| `config.kio.kasten.io`       | `policies`, `profiles`                 | `get`, `list`, `watch`       |
| `actions.kio.kasten.io`      | `runactions`, `exportactions`          | `get`, `list`, `create`, `watch` |
| `apps.kio.kasten.io`         | `restorepoints`, `clusterrestorepoints`| `get`, `list`, `delete`      |

## Accessing the UI

Via the OpenShift Route:
```
https://kasten-cicd.apps.openshift2.lab.home
```

Or via Tailscale if the tailnet ingress is configured in the cluster.
