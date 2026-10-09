import { Component, lazy, memo, Suspense, type ReactNode } from "react";
import { loadPageModule } from "./pageLoad";
import "./assistant-rendering.css";

// Module-level promise: only requested once the first nonempty reply is actually rendered.
const MarkdownContent = lazy(() => loadPageModule(() => import("./MarkdownContent")));
function PlainText({ text }: { text: string }) {
  return <div className="agent-plain-text" data-markdown="plain">{text}</div>;
}
class TextBoundary extends Component<{ text: string; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    if (this.state.failed) return <>
      <PlainText text={this.props.text} />
      <small className="agent-format-note" role="status">格式化视图暂不可用，已保留纯文本回答。不会重新发送请求。</small>
    </>;
    return this.props.children;
  }
}
/** A slow/broken optional formatter must not hide text, navigation or business confirmations. */
export const AssistantText = memo(function AssistantText({ text }: { text: string }) {
  if (!text) return null;
  return <TextBoundary text={text}>
    <Suspense fallback={<PlainText text={text} />}><MarkdownContent text={text} /></Suspense>
  </TextBoundary>;
});
