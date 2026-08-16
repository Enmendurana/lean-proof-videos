/**
 * Make literal identifier text safe for KaTeX without changing the source
 * LaTeX used by the semantic matcher. LeanTeX can emit names such as
 * `erdos_f` inside `\\text{...}`; KaTeX would otherwise interpret `_` as a
 * math subscript and display an error.
 */
export const sanitizeKatexTextCommands = (latex: string): string =>
  latex
  // Keep logical equivalence and implication visually balanced. The proof
  // trace retains its original token identity; normalization happens only at
  // the final KaTeX display boundary.
  .replace(/(?:⇔|↔|\\(?:iff|Leftrightarrow)(?![A-Za-z]))/g, String.raw`\Longleftrightarrow `)
  .replace(/(?:⇒|\\(?:implies|Rightarrow)(?![A-Za-z]))/g, String.raw`\Longrightarrow `)
  .replace(
    /\\(text|operatorname)\{([^{}]*)\}/g,
    (_whole, command: string, body: string) => {
      const escaped = body.replace(
        /(^|[^\\])([_%&#$])/g,
        (_match, prefix: string, character: string) => `${prefix}\\${character}`,
      );
      return `\\${command}{${escaped}}`;
    },
  );
