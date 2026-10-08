# Paper 2 — Topology, connectivity and routing impact

**Status:** proposed research; independent of Paper 1's classifier-accuracy claim. **Reference:** Road Matcher `v1.3.0+87fe`.
**Question:** Do expert-approved road-connection corrections improve network connectivity and practical routing compared with the uncorrected centerlines?

## Existing implementation (read only as needed)
- `app/core/junctions.py`: spatially grouped, conflict-free manual junction proposals and endpoint/interior-vertex edits.
- `app/core/pipeline.py`: `_prepare_junction_round`, `record_junction_decision`, accepted-junction application, `_finalize_junction_outputs`; GeoJSON and connection/decision audits.
- `tests/test_junctions.py` and `tests/test_pipeline_recovery_and_finalization.py`: synthetic geometry and output behavior.
- **Boundary:** this paper evaluates consequences of human-approved topology edits, not a fully automatic road network or certified legal-truck routing.

## Research requirements
1. Freeze an application revision and construct paired **before/after** networks from identical source data; change only verified connection topology.
2. Keep CRS, graph construction, travel direction/restrictions, impedance and routing settings constant; document unmatched restrictions or missing speed data.
3. Compare connected components, isolated segments, cross-boundary links, reachable regions and junction degrees.
4. Use prespecified cross-boundary origin–destination pairs plus within-area controls; evaluate route solvability, distances, detour ratios and continuity. Add identical single-vehicle routes and multi-vehicle VRPs in ArcGIS Pro if licenses/data permit.
5. Manually audit new connections and changed paths, especially bridges, ramps and divided roads; report regressions, paired differences and uncertainty.

## Paper-ready gate
Versioned networks, reproducible OD/VRP scenarios, controlled before/after analysis, independently checked changed links, and documented improvements **and** failures.

**Non-goal:** repackage Paper 1's rejection metrics as routing evidence; modify production routing or bundle ArcGIS into Road Matcher.
