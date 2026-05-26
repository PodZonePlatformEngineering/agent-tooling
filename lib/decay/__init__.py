"""decay — context-decay detector package (PROJ-038).

Structural-only (zero-LLM) detection of six decay categories across a design's
trajectory: oscillation, lost decisions, over-emphasis, off-topic noise,
cross-agent briefing gaps, terminology drift. See
`planning/projects/PROJ-038-decay-detector/spec.md` v1.3.
"""

from .events import DecayEvent, severity_for_span  # noqa: F401
from .manifest import Manifest, ManifestEntry, load_manifest  # noqa: F401
from .runner import run_detection, run_batch, run_trajectory_replay  # noqa: F401
