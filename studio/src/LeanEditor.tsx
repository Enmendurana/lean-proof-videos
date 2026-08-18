import Editor, {loader, type BeforeMount} from '@monaco-editor/react';
import * as monacoLocal from 'monaco-editor/esm/vs/editor/editor.api';

loader.config({monaco: monacoLocal});

const configureMonaco: BeforeMount = (monaco) => {
  monaco.languages.register({id: 'lean4'});
  monaco.languages.setMonarchTokensProvider('lean4', {
    keywords: ['theorem', 'lemma', 'def', 'example', 'by', 'where', 'let', 'have', 'show', 'from', 'match', 'with', 'if', 'then', 'else', 'fun', 'forall', 'exists', 'inductive', 'structure', 'namespace', 'section', 'variable', 'import', 'open'],
    tokenizer: {root: [
      [/--.*$/, 'comment'], [/\/-/, {token: 'comment', next: '@comment'}],
      [/"([^"\\]|\\.)*"/, 'string'], [/[A-Za-z_][\w']*/, {cases: {'@keywords': 'keyword', '@default': 'identifier'}}],
      [/[0-9]+(?:\.[0-9]+)?/, 'number'], [/[∀∃→↔≤≥≠∈∉⊢:=+*\-/<>]/, 'operator'],
    ], comment: [[/[^/-]+/, 'comment'], [/\/-/, 'comment', '@push'], [/-\//, 'comment', '@pop'], [/[/-]/, 'comment']]},
  });
  monaco.editor.defineTheme('blackboard', {base: 'vs-dark', inherit: true, rules: [
    {token: 'keyword', foreground: 'D5A45C'}, {token: 'comment', foreground: '70817A'},
    {token: 'operator', foreground: 'C9E4D9'}, {token: 'number', foreground: 'B9C8F2'},
  ], colors: {'editor.background': '#0b0f0e', 'editorLineNumber.foreground': '#42504b', 'editorCursor.foreground': '#e0ae62', 'editor.selectionBackground': '#34544988'}});
};

export default function LeanEditor({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  return <Editor
    language="lean4"
    theme="blackboard"
    value={value}
    onChange={(next) => onChange(next ?? '')}
    beforeMount={configureMonaco}
    options={{
      fontFamily: "'Cascadia Code', Consolas, monospace",
      fontSize: 14,
      minimap: {enabled: false},
      smoothScrolling: true,
      padding: {top: 18},
      automaticLayout: true,
    }}
  />;
}
