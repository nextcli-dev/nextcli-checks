## Answer
Give your final answer as valid JSON with this exact schema and no other text:
{
  "verdict": "PASS" or "FAIL",
  "summary": "one or two sentences on the result",
  "complete": true,
  "findings": [
    {
      "file": "path/to/file.ext",
      "line": 12,
      "title": "short title of the problem",
      "details": "markdown: what is wrong and why it matters. Show the problem and a fix in fenced code blocks when that helps."
    }
  ]
}
Rules for the answer:
- Use exactly these keys: "verdict", "summary", "complete" and "findings", and in each finding "file", "line", "title" and "details". Do not rename them, nest them or add others. An answer without "verdict" is discarded.
- "file" is the path from the repository root. "line" is a line number in the new version of the file; leave it out if the finding is about the whole file.
- List the most important findings first.
- Set "complete" to false if you stopped before the remaining changed files, and say in "summary" what you did not check. Still return what you did check. A path you could not read is not a reason to set "complete" to false with no findings.
- A PASS verdict must have an empty findings array.
