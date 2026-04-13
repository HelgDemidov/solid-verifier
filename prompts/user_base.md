Analyze the following Python class for potential {candidate_type} design issues related to SOLID.

Target class: {class_name}
File path: {file_path}

Code under review:
```python
{source_code}
```

Instructions:
- Evaluate only the code shown above and the class named above.
- Focus only on OCP and LSP concerns.
- Report only issues supported by concrete evidence in the code.
- Do not treat ordinary conditionals, validation checks, or incomplete context as automatic design violations.
- Prefer specific behavioral or structural observations over generic best-practice commentary.
- If the evidence is insufficient for a credible finding, return no finding rather than speculate.

Remember: your entire response must be a single valid JSON object with key "findings" containing an array.
If you find no issues, return exactly: {"findings": []}
Do not add any text, explanation, or markdown outside the JSON object.