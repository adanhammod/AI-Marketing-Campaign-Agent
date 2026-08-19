# Cinematic music build input

The CINEMATIC_TEXT_AD path requires a music bed. Before building the worker,
place one redistribution-approved MP3 at
services/worker/assets/music/cinematic.mp3.

The MP3 is intentionally gitignored because the locally proven Mixkit download
has not been cleared for redistribution from this repository. CI authenticates
through GitHub OIDC and restores the exact operator-configured private S3 object
from CINEMATIC_MUSIC_ARTIFACT_URI before docker build. Terraform receives the
matching music_asset_object_arn solely to grant that object-level GetObject
permission. No bucket or key is invented by this repository.

The image build fails when the file is absent or empty.

SFX remain optional and are not packaged. Leave SFX_LIBRARY_PATH unset.
