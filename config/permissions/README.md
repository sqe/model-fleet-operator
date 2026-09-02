# Permission profiles

The files in this directory cover two different identities. Keep them separate.

| Identity | Files | Purpose |
|---|---|---|
| Model and training workload | `aws.example.json`, `gcp.example.json`, `bare-metal.example.json` | Read model or dataset objects and write run artifacts. |
| Slack quota requester | `aws-quota-requester-policy.example.json`, `gcp-quota-requester-role.example.yaml` | Inspect and submit approved cloud quota requests. |

Render a workload profile after choosing the platform:

```bash
cp config/permissions/aws.example.json permissions.json
# Replace every account, role, bucket, and prefix with approved values.
make permissions-render PERMISSION_CONFIG=permissions.json
```

Quota profiles are deliberately not applied by the renderer. They authorize an
external cloud change and must go through the platform team's IAM review. Copy
the matching example, narrow it to the approved account or project, attach it to
the Slack pod identity, and enable `slack.quotaRequests.enabled` only after that
review. The AWS example limits the write action to one quota ARN. The GCP custom
role must be bound only on projects where Slack-based requests are permitted.

These examples are starting points, not universal policies. AWS quota ARNs are
region, account, service, and quota specific. Google Cloud authorization is
scoped by the IAM binding on the target project.
