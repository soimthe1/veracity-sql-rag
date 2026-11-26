import { Component, ReactNode } from "react";
import { useI18n } from "../i18n";

/**
 * The Health tab crash (rendering a JSON object as a child with no
 * guard) took the ENTIRE app down — sidebar included — because nothing
 * caught the exception. React only stops that at an error boundary, and
 * there wasn't one anywhere. App.tsx mounts one of these per tab (each
 * `tab === "X" && <ErrorBoundary>...` only exists while that tab is
 * active), so a bug in one tab's rendering only blanks that tab, never
 * navigation itself — and switching away and back remounts a fresh
 * instance, giving it another chance once, e.g., new data has arrived.
 */
interface Props {
  tabName: string;
  children: ReactNode;
}

interface State {
  error: Error | null;
}

// A class component can't call hooks itself, so the translated message
// is rendered by this small functional child instead — same trick
// StatusBadge-adjacent components use nowhere else needs, but a class
// error boundary is the one spot in this app that has to.
function ErrorBoundaryMessage({ tabName, error }: { tabName: string; error: Error }) {
  const { t } = useI18n();
  return <div className="banner error">{t("errorBoundary.message", { tab: tabName, error: error.message })}</div>;
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  render() {
    if (this.state.error) {
      return <ErrorBoundaryMessage tabName={this.props.tabName} error={this.state.error} />;
    }
    return this.props.children;
  }
}
