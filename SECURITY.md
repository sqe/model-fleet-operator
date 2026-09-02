# Security policy

Please report vulnerabilities privately to the repository maintainers rather
than opening a public issue. Include affected versions, reproduction steps, and
the impact you observed.

Creating a Model Fleet resource is equivalent to permission to run a container
in its namespace. Cluster administrators should limit CRD write access, enforce
admission policies for image registries and pod security, and grant workload
service accounts separately. The operator does not copy Slack or cloud secrets
into managed workloads.
