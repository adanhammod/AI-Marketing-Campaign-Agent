# Kubernetes deployment

The namespaces directory is cluster bootstrap state. Argo CD tracks dev from
dev and prod from main. Dev exposes the frontend through
NodePort 30080; the Terraform ALB targets that port. Production remains internal
until a domain/TLS decision is made.

The HyperFrames worker requests 1 CPU, 2 GiB RAM and 8 GiB ephemeral storage,
with limits of 2 CPU, 3 GiB and 16 GiB. A 1 GiB memory-backed /dev/shm and a
regular /app/tmp emptyDir isolate Chrome and render scratch data. Artifacts
remain durable only in S3.

The worker image requires a licensed cinematic MP3 staged before build. SFX are
optional and SFX_LIBRARY_PATH is intentionally unset.

The m7i.xlarge worker has 4 vCPU and 16 GiB RAM. Declared application requests
total 1.3 CPU and approximately 2.3 GiB RAM, leaving about 2.7 CPU and more than
12 GiB before kube/system reservations. The HyperFrames pod limit is 2 CPU and
3 GiB; even at limits the three application pods remain below 3 CPU and 4 GiB.
