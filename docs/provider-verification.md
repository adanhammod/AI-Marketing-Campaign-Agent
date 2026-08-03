# Provider Verification

## Verification Status

| Provider | Status | Verified | MVP Decision |
|---|---|---|---|
| Amazon Bedrock | Available | 2026-07-28 | Use Amazon Nova Lite in `us-east-1` for text generation. |
| Image Generator MCP | Schema verified; provider invocation blocked | 2026-07-28 | Use `mcp-image` with Gemini after paid API access and project quota are verified. |
| HyperFrames MCP | Capability-verified; OAuth-blocked, not end-to-end provider-verified | 2026-07-28 | Contract work may proceed; authorize HeyGen, capture runtime schemas, verify credits, and complete one minimal render before final integration. |

## Amazon Bedrock

### Goal

Verify AWS identity and credential resolution, model and region availability, runtime authorization, quotas, and estimated text-generation cost before freezing the model adapter contract.

### Result

- AWS authentication succeeds using an IAM user and the shared credentials file.
- The configured region is `us-east-1`.
- The Bedrock control plane and runtime are reachable.
- `amazon.nova-lite-v1:0` is active and supports on-demand inference in `us-east-1`.
- Explicit model availability reports `AUTHORIZED` and `AVAILABLE`.
- A minimal Converse invocation returned `BEDROCK_OK`.
- Invocation metrics reported 11 input tokens, 5 output tokens, and 248 ms latency.

No credential values, AWS account identifiers, or IAM principal names are stored in this document.

### MVP Model and Region

| Setting | Value | Reason |
|---|---|---|
| AWS region | `us-east-1` | Existing configured region; runtime invocation verified; direct Nova Lite availability; strong default quota. |
| Model | `amazon.nova-lite-v1:0` | Low-cost on-demand text model suitable for structured strategy, copy, and storyboard generation. |
| API | Bedrock Converse API | Common message interface and usage metrics. |
| Inference mode | In-region on-demand | Simplest verified MVP path; no provisioned throughput commitment. |
| Cross-region fallback | `us.amazon.nova-lite-v1:0` | Optional only if later load testing demonstrates a need and data-routing behavior is approved. |

The model ID and region must remain configuration values rather than hardcoded business logic.

### Credential Posture

The current local CLI resolves long-lived credentials from `~/.aws/credentials`. This is acceptable only for local Week 1 verification.

Required implementation posture:

- Never commit or copy shared credential files.
- Never store AWS keys in container images or Kubernetes manifests.
- Use least-privilege EC2 instance profiles for the kubeadm MVP.
- Use GitHub Actions OIDC with a scoped deployment role rather than long-lived CI keys.
- Require IMDSv2 on EC2.
- Rotate the current local IAM-user credentials if they have been exposed outside the developer workstation.

### Quotas

Observed and documented quota information for Amazon Nova Lite in `us-east-1`:

| Quota | Value | Source/Status |
|---|---:|---|
| On-demand inference requests per minute | 2,000 RPM | AWS General Reference default for `us-east-1`. |
| Cross-region inference requests per minute | 4,000 RPM | Visible through this account's Service Quotas API. |
| Cross-region tokens per minute | 8,000,000 TPM | AWS General Reference default for US regions. |
| Model invocation tokens per day | 5,760,000,000 | AWS General Reference; new accounts may receive reduced quotas. |

The single-user MVP is far below these published limits. Runtime throttling must still be classified as retryable and handled with bounded exponential backoff and jitter. Quotas must be rechecked before the final demo because AWS may apply account-specific limits.

### Estimated Bedrock Usage Cost

Pricing basis for Amazon Nova Lite standard on-demand inference:

- Input: USD 0.06 per 1 million tokens.
- Output: USD 0.24 per 1 million tokens.

Planning assumption per generated campaign version:

- Five model calls across analysis, strategy, copy, storyboard, and validation.
- Up to 25,000 aggregate input tokens.
- Up to 10,000 aggregate output tokens.

Estimated Bedrock text cost:

```text
(25,000 / 1,000,000 × $0.06)
+ (10,000 / 1,000,000 × $0.24)
= $0.0039 per campaign version
```

| Scenario | Estimated Bedrock Text Cost |
|---|---:|
| One campaign version | USD 0.0039 |
| 20 development/test versions | USD 0.078 |
| 50 development/test versions | USD 0.195 |
| 50 versions plus 25% contingency | USD 0.244 |

These figures cover Bedrock text inference only. Image generation, HyperFrames rendering, EC2, data transfer, S3, DynamoDB, SQS, ECR, monitoring, taxes, and retries are excluded and will be estimated separately.

### Commands

```bash
aws configure list
aws sts get-caller-identity
aws bedrock list-foundation-models --region us-east-1
aws bedrock get-foundation-model-availability \
  --region us-east-1 \
  --model-id amazon.nova-lite-v1:0
aws bedrock-runtime converse \
  --region us-east-1 \
  --model-id amazon.nova-lite-v1:0 \
  --messages 'role=user,content=[{text=Reply_with_exactly_BEDROCK_OK}]' \
  --inference-config 'maxTokens=20,temperature=0'
aws service-quotas list-service-quotas \
  --service-code bedrock \
  --region us-east-1
```

### Verification Evidence

Expected minimal invocation result:

```json
{
  "text": "BEDROCK_OK",
  "input_tokens": 11,
  "output_tokens": 5,
  "latency_ms": 248
}
```

Live verification succeeded on 2026-07-28.

### Fallback Behavior

Amazon Bedrock is available; no production fallback provider is approved. Deterministic mocked Bedrock responses may be used for local and CI contract tests. If Bedrock is unavailable during the live demonstration, the backup demo may use previously generated structured outputs while still demonstrating queued execution, persisted checkpoints and metadata, review, approval, and finalization. The fallback must be disclosed in the UI or presentation.

### Acceptance Criteria

- [x] AWS credentials resolve without exposing secret values.
- [x] Required region is selected and documented.
- [x] Candidate model appears active in the regional catalog.
- [x] Model authorization and entitlement are available.
- [x] A real minimal runtime invocation succeeds.
- [x] Relevant default/account-visible quotas are documented.
- [x] MVP token and cost assumptions are documented.
- [x] Credential risks and fallback behavior are explicit.

Task 1 is verified and complete.


## Image Generator MCP (`shinpr/mcp-image`)

### Goal

Verify installation, runtime/provider boundaries, actual MCP schemas, required capabilities, credentials, limits, costs, timeout behavior, a minimal invocation, and live-demo fallback without implementing the application.

### Installation and Access Method

| Property | Verified Value |
|---|---|
| Repository / npm | `shinpr/mcp-image` / `mcp-image` |
| Verified package | `0.11.4`; Node.js >=22 |
| Start command | `npx -y mcp-image` |
| Transport | Local stdio MCP |
| Runtime identity | `mcp-image-server` version `0.1.0` |
| MCP protocol | `2025-06-18` |
| External provider | Paid Gemini API by default; optional OpenAI API |

The Node server runs locally, calls the remote provider, saves the returned image under `IMAGE_OUTPUT_DIR`, and returns a file resource. It is not a remotely hosted MCP. In Kubernetes it will require a pinned local workload/wrapper; pod-local output is temporary and accepted assets must be copied to S3.

```toml
[mcp_servers.mcp-image]
command = "npx"
args = ["-y", "mcp-image"]

[mcp_servers.mcp-image.env]
GEMINI_API_KEY = "<injected-secret>"
IMAGE_OUTPUT_DIR = "/absolute/non-sensitive/output/path"
IMAGE_QUALITY = "fast"
```

### Available Tools and Exact Schemas

Live MCP `tools/list` exposed exactly one tool: `generate_image`.

```json
{
  "name": "generate_image",
  "description": "Generate image with specified prompt and optional parameters",
  "inputSchema": {
    "type": "object",
    "properties": {
      "prompt": {"type": "string", "description": "The prompt for image generation (English recommended for optimal structured prompt enhancement)"},
      "fileName": {"type": "string", "description": "Custom file name for the output image. Auto-generated if not specified."},
      "inputImagePath": {"type": "string", "description": "Optional absolute path to source image for image-to-image generation. Use when generating variations, style transfers, or similar images based on an existing image (must be an absolute path)"},
      "blendImages": {"type": "boolean", "description": "Enable multi-image blending for combining multiple visual elements naturally. Use when prompt mentions multiple subjects or composite scenes"},
      "maintainCharacterConsistency": {"type": "boolean", "description": "Maintain character appearance consistency. Enable when generating same character in different poses/scenes"},
      "useWorldKnowledge": {"type": "boolean", "description": "Use real-world knowledge for accurate context. Enable for historical figures, landmarks, or factual scenarios"},
      "useGoogleSearch": {"type": "boolean", "description": "Enable Google Search grounding to access real-time web information for factually accurate image generation. Use when prompt requires current or time-sensitive data that may have changed since the model's knowledge cutoff. Leave disabled for creative, fictional, historical, or timeless content."},
      "aspectRatio": {"type": "string", "description": "Aspect ratio for the generated image", "enum": ["1:1", "1:4", "1:8", "2:3", "3:2", "3:4", "4:1", "4:3", "4:5", "5:4", "8:1", "9:16", "16:9", "21:9"]},
      "imageSize": {"type": "string", "description": "Image resolution for high-quality output. Specify 1K, 2K, or 4K when you need specific resolution. Leave unspecified for standard quality.", "enum": ["1K", "2K", "4K"]},
      "purpose": {"type": "string", "description": "Intended use for the image. Influences lighting, composition, and detail level to match the context."},
      "quality": {"type": "string", "description": "Quality preset controlling speed/fidelity tradeoff.", "enum": ["fast", "balanced", "quality"]}
    },
    "required": ["prompt"]
  }
}
```

The live schema publishes no `outputSchema`. The documented success output is a resource containing `uri`, `name`, and `mimeType`, plus metadata containing `model`, `provider`, `processingTime`, and `timestamp`. Consumers must also detect errors encoded inside text content: the test returned a configuration error as text while MCP `isError` was `false`.

### Supported Capabilities

| Capability | Status | Limitation |
|---|---|---|
| Text-to-image | Supported | `prompt` required. |
| 9:16 vertical | Schema-supported | Exact `9:16` enum; provider output not live-verified. |
| Image editing | Supported | One absolute PNG/JPEG/WebP input path; documented max 10 MB. |
| Reference image | Supported | One local `inputImagePath`; no URL/S3 contract. |
| Character consistency | Best effort | Boolean hint only; no stable character ID or seed. |
| Multi-image blending | Ambiguous | Flag exists, but schema exposes only one input path. |
| Output format selection | Not explicit | Docs mention PNG/JPEG/WebP, but there is no format parameter; OpenAI adapter pins PNG. |
| Resolution | Supported | `1K`, `2K`, `4K`. |
| Aspect ratios | Supported | Fourteen enumerated ratios. |
| Search grounding | Gemini only | Unsupported in OpenAI mode. |
| Quality | Supported | `fast`, `balanced`, `quality`. |

### Required Credentials and Provider Mapping

| Variable | Requirement |
|---|---|
| `IMAGE_PROVIDER` | Optional: `gemini` default or `openai`. |
| `GEMINI_API_KEY` | Required for Gemini paid image access. |
| `OPENAI_API_KEY` | Required for OpenAI; organization verification may be required. |
| `IMAGE_OUTPUT_DIR` | Absolute writable directory recommended; default `./output`. |
| `IMAGE_QUALITY` | `fast` default; `balanced` or `quality`. |
| `SKIP_PROMPT_ENHANCEMENT` | `true` skips the separate optimization call. |

Gemini `fast`/`balanced` use Gemini 3.1 Flash Image; balanced adds thinking. `quality` uses Gemini 3 Pro Image. OpenAI mode fixes prompt enhancement to `gpt-4o-mini` and generation to `gpt-image-2`.

No Gemini or OpenAI key was available in the actual verification process. Future credentials must be secret-store injected and absent from Git, images, Terraform state, logs, and docs.

### Duration, Quotas, Timeout, and Cost

- Documented `fast` duration is about 30-40 seconds; balanced, quality, and 2K/4K are slower.
- Google limits vary by project/tier across RPM, TPM, RPD, and image IPM. Active limits could not be inspected without a project credential.
- Published source contains a fixed 30,000 ms default and no timeout environment variable. Prompt enhancement has an explicit timeout; the image-generation call has no clear abort deadline.
- Proposed later worker contract: 180-second deadline per image attempt, bounded retries, and idempotency verification.

| Google Standard Model/Size | Per Image | Three Images |
|---|---:|---:|
| Gemini 3.1 Flash Image 1K | USD 0.067 | USD 0.201 |
| Gemini 3.1 Flash Image 2K | USD 0.101 | USD 0.303 |
| Gemini 3.1 Flash Image 4K | USD 0.151 | USD 0.453 |
| Gemini 3 Pro Image 1K/2K | USD 0.134 | USD 0.402 |
| Gemini 3 Pro Image 4K | USD 0.240 | USD 0.720 |

Prompt enhancement, thinking, references, search, retries, and taxes add cost. MVP default recommendation: three `fast` 1K images.

### Real Invocation Result

```json
{
  "prompt": "A simple studio photograph of a reusable coffee cup on a neutral background, no text",
  "quality": "fast",
  "aspectRatio": "9:16",
  "imageSize": "1K",
  "fileName": "task2-verification.png",
  "purpose": "marketing campaign test"
}
```

| Field | Result |
|---|---|
| MCP/tool reached | Yes |
| Provider generation | No |
| Error | Sanitized `CONFIG_ERROR`: Gemini credential missing |
| Duration | 0.013 seconds |
| Output type | MCP text containing a structured error |
| Provider request | Not sent |
| Generated file | None |

This is a credential blocker, not evidence of provider downtime.

### Risks and Limitations

- npm version `0.11.4` differs from reported server version `0.1.0`.
- Community package and unpinned `npx` execution create supply-chain risk; pin a reviewed version/digest later.
- No formal output schema; tool failures may not set MCP `isError`.
- Character consistency is not guaranteed.
- Output format is not explicitly selectable.
- Absolute file paths couple the client and server filesystem.
- Local file URIs require validation and S3 transfer.
- Normal generation may exceed the package's 30-second default.
- Provider models, preview status, prices, and quotas may change.

### Demo Fallback

If the MCP or Gemini is unavailable, use previously generated checksum-verified S3 assets, persist the intended parameters/provider metadata/fallback reason with `fallback_asset=true`, and continue checkpointing, review, approval, and finalization. Do not claim fresh generation, silently change providers, or hide fallback use. OpenAI requires separate verification and is not an automatic fallback.

### Verification Evidence

- npm metadata, origin, version, dependencies, and Node requirement verified.
- Live MCP initialization and `tools/list` succeeded.
- Published source was inspected ephemerally for configuration, model, MIME, format, and timeout behavior.
- One MCP call was attempted and failed before provider access due to missing credentials.
- No image or repository artifact was created.
- Secret scan must find no keys, tokens, account IDs, or developer-specific paths.

### Acceptance Criteria

- [x] Installation, start command, and local/remote boundary verified.
- [x] All live tools and exact input schema recorded.
- [x] Documented output and missing runtime `outputSchema` recorded.
- [x] Capability matrix completed.
- [x] Credentials and environment identified.
- [x] Limits, duration, timeout behavior, and costs documented.
- [x] Minimal MCP invocation attempted with evidence recorded.
- [x] Demo fallback defined.
- [ ] Real provider generation succeeds; blocked by unavailable paid credential/project quota.

Task 2 capability verification is complete. Live provider generation remains blocked until a valid paid Gemini API key and project quota are available.


## HyperFrames MCP

### Goal

Verify the official HyperFrames MCP connection, hosted/local boundary, six-tool surface, exact MVP subset, composition/video capabilities, render lifecycle, credentials, limits, credits, a minimal render when authorized, and safe demo fallback without implementing the application.

### Installation or Access Method

| Property | Verified Value |
|---|---|
| Product | HyperFrames MCP by HeyGen |
| Type | Hosted remote MCP; beta |
| Production endpoint | `https://mcp.heygen.com/mcp/hyperframes` |
| Local install | None for hosted MCP |
| Authentication | HeyGen OAuth bearer token |
| Authorization server | `https://api2.heygen.com` |
| OAuth scopes advertised | `openid`, `profile`, `email` |
| Account boundary | Composition belongs to the HeyGen space that created it |
| Open-source alternative | `heygen-com/hyperframes` CLI; separate from hosted MCP |

Connection is made through the host connector catalog or a custom MCP connector pointed at the production endpoint. The official inspection command is:

```bash
npx @modelcontextprotocol/inspector \
  npx -y mcp-remote https://mcp.heygen.com/mcp/hyperframes
```

It opens an interactive HeyGen OAuth flow. The endpoint redirects to its canonical trailing-slash URL and then returns `401 Bearer token required` without authorization. No API-key environment variable is the official hosted-MCP authentication method.

### Available Tools

Official documentation lists six tools:

| Tool | Purpose | Cost Class |
|---|---|---|
| `compose` | Create a composition or conversationally edit an existing one. | Author credits |
| `list_compositions` | List owned compositions, newest first, paginated. | Free |
| `get_composition` | Fetch one composition and its player/preview metadata. | Free |
| `render_video` | Submit a cloud render. | Render credits |
| `get_render_status` | Poll an asynchronous render job. | Free |
| `get_credits` | Return the current tier and remaining credits. | Free |

### Exact MVP Tool Subset

The approved MVP integration surface is:

1. `compose` - author one immutable video composition from the approved campaign version.
2. `render_video` - submit one render for that exact composition.
3. `get_render_status` - poll until success, failure, or the application deadline.

`list_compositions`, `get_composition`, and `get_credits` are operational/support tools. `get_credits` should be used during verification and pre-demo checks, but it is not required in the campaign-generation graph.

### Tool Schemas

The official public guide documents semantic behavior but does not publish complete JSON input/output schemas. The protected MCP endpoint requires OAuth before `tools/list`; no authorized connector or bearer token is available in this environment. Therefore exact runtime schemas cannot be captured safely yet.

| Tool | Publicly Documented Contract | Exact Runtime Schema Status |
|---|---|---|
| `compose` | Natural-language create/edit request; returns composition reference containing ID, title, thumbnail, plus player widget; emits progress notifications. | Blocked by OAuth |
| `list_compositions` | Paginated list, newest first. | Blocked by OAuth |
| `get_composition` | Fetch by owned composition; returns metadata and player widget. | Blocked by OAuth |
| `render_video` | Composition render; formats `mp4`, `webm`, `mov`; FPS `24`, `30`, `60`; returns output URL within 25 seconds or `job_id`. | Blocked by OAuth |
| `get_render_status` | Poll by render job; documented active status includes `rendering`. | Blocked by OAuth |
| `get_credits` | Returns tier and remaining credits. | Blocked by OAuth |

No guessed field names beyond documented `composition_id` and `job_id` are treated as contracts. Before implementation, an authorized MCP Inspector session must export `tools/list` and sanitized call responses for all six tools.

### Supported Composition and Video Capabilities

| Capability | Verified Support | Notes |
|---|---|---|
| Script/storyboard | Yes through `compose` prompt | Natural-language agent input, not a published typed storyboard object. |
| Images | Limited | Existing assets uploaded to the HeyGen account can be referenced by name; MCP cannot upload new binaries from chat. |
| Text overlays/captions | Yes | Agent handles typography, layout, overlays, and captions. |
| Scene duration/timing | Yes | Natural-language total/scene timing; exact schema not public. |
| 9:16 vertical | Yes | Official ratios include `16:9`, `9:16`, `1:1`, `4:5`; others use closest match. |
| Audio/narration | Partially documented | Agent supports audio-reactive work and hosted asset/voice behavior, but exact audio/narration input schema is not public. |
| Transitions | Yes | Agent selects/applies transitions; conversational edits supported. |
| Output format | Yes | `mp4` H.264 default, `webm` VP9, `mov` ProRes. |
| Frame rate | Yes | `24`, `30` default, `60`. |
| Output resolution | Plan-dependent/unclear for MCP | HeyGen plans advertise 720p/1080p/4K generally, but MCP `render_video` resolution schema is not public. |
| Local rendering | No for hosted MCP | All hosted MCP renders run on HeyGen infrastructure. |

For the MVP, request a 15-second `9:16` composition, render `mp4` at 30 FPS, and independently validate downloaded output with FFprobe before S3 acceptance. Resolution remains a verification gate until the runtime schema is captured.

### Render Lifecycle and Failure Behavior

Officially documented lifecycle:

```text
compose
  -> composition reference
  -> render_video
       -> completed within 25 seconds: rendered output URL
       -> otherwise: job_id
            -> get_render_status(job_id)
                 -> rendering
                 -> completed + output URL
                 -> failed/error
```

- Progress notifications are emitted during `compose` and `render_video`.
- Normal renders take approximately 10-90 seconds depending on duration, FPS, and format.
- `rendering` beyond five minutes is documented as stuck.
- Complex `compose` calls can exceed 30 seconds; a host timeout does not prove the backend stopped. The official recovery is to wait and query compositions.
- Out-of-credit errors include an upgrade URL.
- Composition-not-found occurs when the composition belongs to a different HeyGen space.
- Exact terminal status enum, error object, retryability fields, URL expiry, and retention are not publicly documented and remain blocked pending authorized schema/call capture.

Proposed later application boundary: 60-second `compose` client deadline with reconciliation through listing; five-minute render deadline; poll every five seconds with jitter; persist provider IDs and sanitized errors; never submit a second render until idempotency/reconciliation checks complete.

### Required Credentials and External Services

- A HeyGen account with HyperFrames MCP entitlement.
- Interactive OAuth authorization through a supported MCP host or Inspector.
- Sufficient author and render credits.
- A HeyGen space owns compositions and assets; its identifier must be treated as sensitive metadata.
- Existing images, logos, fonts, or audio must be uploaded through HeyGen before the MCP can reference them.
- HeyGen's hosted composition agent, cloud renderer, asset library, OAuth, and credit systems are external dependencies.

No HyperFrames/HeyGen environment credential, connector reference, authorized OAuth session, account ID, or space/workspace ID was available during verification.

### Credits, Pricing, Limits, and Retention

| Item | Verified Finding |
|---|---|
| Billing model | `compose` consumes author credits; `render_video` consumes render credits; read/status/credit tools are free. |
| Exact HyperFrames credit cost | Not published in the MCP guide; must be read from `get_credits` and the authorized product UI. |
| General HeyGen plans | Free trial quota; Creator starts at USD 29/month with 600 credits; Pro starts at USD 49/month with 1,000 credits. These are not proof of HyperFrames per-render cost. |
| Rate limits/concurrency | Not published for the MCP. |
| Maximum MCP video duration | Not published. General HeyGen plan maximums must not be assumed to be MCP limits. |
| Expected render time | Usually 10-90 seconds. |
| Stuck threshold | More than five minutes in `rendering`. |
| Fast-path response | Direct output URL if finished within 25 seconds; otherwise `job_id`. |
| Output retention / URL expiry | Not published. Download and copy successful output immediately to private S3. |

The MVP cost cannot be estimated responsibly until an authorized `get_credits` call and one minimal compose/render show credit deltas. Budget planning must keep HyperFrames cost marked `TBD`, with no automatic paid retry storm.

### Real Invocation Result

| Step | Result |
|---|---|
| Hosted endpoint reachability | Passed |
| OAuth resource metadata | Passed |
| Authorized `tools/list` | Blocked |
| `get_credits` | Blocked |
| Minimal `compose` | Not attempted; unauthorized |
| `render_video` | Not attempted; no composition/authorization |
| `get_render_status` | Not attempted; no job ID |
| Render duration/output | None |

Exact blocker: the production MCP requires a HeyGen OAuth bearer token, and this environment has no connected HyperFrames MCP session or HeyGen account authorization. Attempting paid authoring/rendering without that authorization is neither possible nor appropriate.

### Risks and Limitations

- Hosted MCP is beta; tools, schemas, prices, and behavior may change.
- Exact runtime schemas and status/error enums are unavailable before OAuth.
- Natural-language `compose` is less deterministic than a typed storyboard contract.
- No binary upload through MCP; project S3 images cannot be passed directly without an explicit HeyGen upload step.
- Composition ownership is tied to one HeyGen space.
- Hosted output URL retention/expiry is unknown.
- Rate limits, concurrency, maximum duration, resolution control, and exact credits are unpublished.
- Host timeouts may leave successful background compositions/renders, creating duplicate-work risk.
- Widgets are host-dependent; text-only clients receive preview links.
- Provider output must be downloaded, validated, checksummed, and copied to S3 before it becomes durable project output.

### Demo Fallback

If HyperFrames is unavailable or unauthorized during the live demo:

1. Use a previously rendered, FFprobe-validated, checksum-verified video stored privately in S3.
2. Persist the intended composition request, expected `9:16`/15-second/MP4 parameters, provider name, fallback reason, checksum, and `fallback_asset=true`.
3. Demonstrate SQS delivery, checkpoint/resume, stored provider metadata, review, approval, and finalization.
4. Clearly label the video as a fallback asset; never claim it was rendered live.
5. Do not expose old provider URLs, composition IDs, job IDs, account/space IDs, or private S3 URLs.

### Verification Evidence

- Official HyperFrames MCP guide verified hosted endpoint, OAuth, six tools, costs by class, formats, FPS, lifecycle, timing, and limitations.
- Public OAuth protected-resource metadata verified the resource and authorization server without account data.
- Unauthenticated canonical endpoint returned `401` with bearer-token requirement.
- Tool discovery found no connected HyperFrames capability in this session.
- Environment/config checks found no usable credential or connector reference.
- No composition, render, provider ID, private URL, or local artifact was created.
- Documentation must pass a sensitive-data scan.

### Acceptance Criteria

- [x] Official installation/connection method verified.
- [x] Hosted/local/provider boundary verified.
- [x] Official endpoint and authentication method verified.
- [x] Six documented tools identified.
- [x] Three-tool MVP subset defined.
- [x] Publicly documented composition/video capabilities recorded.
- [x] 9:16 support confirmed.
- [x] Public render lifecycle, timing, and fast-path/job behavior recorded.
- [x] Credentials, ownership, external services, and fallback documented.
- [x] Public pricing/limit facts separated from unknowns.
- [ ] Exact runtime input/output schemas captured; blocked by OAuth.
- [ ] Minimal real compose/render/status flow completed; blocked by OAuth and credits.

Task 3 is accepted as capability-verified but OAuth-blocked. The hosted endpoint, authentication boundary, documented six-tool surface, MVP subset, public capabilities, and documented render lifecycle are verified. Runtime schemas and a real compose/render/status run remain unverified. This external dependency does not block Task 4 contract work; before final HyperFrames integration, authorize a HeyGen account, capture `tools/list` schemas, call `get_credits`, and complete one minimal render without recording sensitive identifiers or URLs.
