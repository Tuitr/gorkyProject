"""S3 buckets of the stand (A1.2). Which service may read or write each one: deploy/seaweedfs/s3.json.

A file reference in the contracts is {bucket, key}: DocRef(bucket=SOURCES_BUCKET, key="attempts/a1.docx").
"""

SOURCES_BUCKET = "sources"
"""Uploaded DOCX and ZIP: the cabinet writes, grading reads."""

RESULTS_BUCKET = "results"
"""Markdown reports: grading writes, the cabinet reads to hand them out."""

ARTIFACTS_BUCKET = "artifacts"
"""Solver code, tests and metadata: the reference service only."""

BUCKETS = (SOURCES_BUCKET, RESULTS_BUCKET, ARTIFACTS_BUCKET)
