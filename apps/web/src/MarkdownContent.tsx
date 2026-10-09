import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** Keep raw HTML and external images disabled, exactly as the original reply renderer. */
export default function MarkdownContent({ text }: { text: string }) {
  return <div data-markdown="ready"><ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={{
    img: () => null,
    a: ({ children, href }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
  }}>{text}</ReactMarkdown></div>;
}
