import { Component, memo, useEffect, useSyncExternalStore, type ReactNode } from "react";
import { loadPageModule } from "./pageLoad";
import { createOptionalModule } from "./optionalModule";
import "./assistant-rendering.css";

// Fetch once on the first nonempty reply. Formatting is an enhancement, not a suspended page.
const formatter = createOptionalModule(() => loadPageModule(() => import("./MarkdownContent")));
function PlainText({ text }: { text: string }) {
  return <div className="agent-plain-text" data-markdown="plain">{text}</div>;
}
function FormatNote() {
  return <small className="agent-format-note" role="status">格式化视图暂不可用，已保留纯文本回答。不会重新发送请求。</small>;
}
class TextBoundary extends Component<{ text: string; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    if (this.state.failed) return <><PlainText text={this.props.text} /><FormatNote /></>;
    return this.props.children;
  }
}
function FormattedText({ text }: { text: string }) {
  const state = useSyncExternalStore(formatter.subscribe, formatter.getSnapshot, formatter.getSnapshot);
  useEffect(() => { void formatter.start(); }, []);
  const Renderer = state.state === "ready" ? state.value.default : null;
  return <TextBoundary text={text}>{Renderer ? <Renderer text={text} /> : <>
    <PlainText text={text} />{state.state === "failed" && <FormatNote />}
  </>}</TextBoundary>;
}
/** A slow/broken optional formatter must not hide text, navigation or business confirmations. */
export const AssistantText = memo(function AssistantText({ text }: { text: string }) {
  return text ? <FormattedText text={text} /> : null;
});
