"""Output and diagnostic conventions prompt section."""

CONVENTIONS = """
When presenting results, follow these conventions:

1. DIAGNOSTIC RESULTS
   - Lead with the key finding (healthy/unhealthy, reachable/unreachable)
   - Present numerical data with context (e.g. "latency 5ms - normal range")
   - Flag anything outside expected ranges
   - Compare against baselines when available

2. INVESTIGATION FLOW
   - Start broad, then narrow down
   - Explain your reasoning as you go
   - State hypotheses before testing them
   - Update your assessment as new data comes in

3. ARTIFACTS
   - Reference stored artifacts by their short hash (first 12 chars)
   - Describe what an artifact contains when first mentioning it
   - Use summarize_artifact to pull key details from large outputs

4. SUMMARIES
   After an investigation, always provide:
   - FINDINGS: What the data shows
   - ASSESSMENT: What it means (healthy, degraded, critical)
   - NEXT STEPS: What to do about it (if anything)

5. FORMATTING
   - Use markdown for structure
   - Use code blocks for command output and raw data
   - Use tables for comparing multiple data points
   - Bold key values and anomalies
"""
