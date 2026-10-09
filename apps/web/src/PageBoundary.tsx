import { Component, Suspense, type ReactNode } from "react";
import { PageModuleError } from "./pageLoad";
import "./page-load.css";

/** Keeps navigation outside the failed route; never automatically reloads or retries writes. */
export class PageBoundary extends Component<{ children: ReactNode }, { failed: boolean; moduleFailure: boolean }> {
  state = { failed: false, moduleFailure: false };
  static getDerivedStateFromError(error: unknown) {
    return { failed: true, moduleFailure: error instanceof PageModuleError };
  }
  render() {
    if (this.state.failed) return (
      <section className="page-load-state" role="alert" aria-labelledby="page-load-title">
        <span className="eyebrow">WORKSPACE</span>
        <h1 id="page-load-title">{this.state.moduleFailure ? "页面加载失败" : "页面暂时无法显示"}</h1>
        <p>请检查网络后手动重新加载页面，也可以使用导航前往其他页面。</p>
        <p className="page-load-note">未保存的输入可能丢失。页面不会自动重发消息或确认操作；已提交的业务请到原记录中核实。</p>
        <button type="button" className="button" onClick={() => window.location.reload()}>重新加载页面</button>
      </section>
    );
    return <Suspense fallback={
      <section className="page-load-state" role="status" aria-live="polite" aria-busy="true">
        <span className="eyebrow">WORKSPACE</span>
        <h1>正在加载页面…</h1>
        <p>正在获取当前页面所需的界面资源，请勿重复提交操作。</p>
        <div className="page-load-skeleton" aria-hidden="true" />
      </section>
    }>{this.props.children}</Suspense>;
  }
}
