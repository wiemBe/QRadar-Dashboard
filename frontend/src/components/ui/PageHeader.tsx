// The one page-title block every route uses.
//
// It owns the document's single <h1>. Centralising it is what makes the
// heading outline correct by construction: pages compose sections beneath it
// starting at <h2>, and the shell deliberately holds no heading at all, so
// there is exactly one <h1> per page and it names the page.
//
// The description is one line of orientation, not documentation. Anything
// longer belongs in the section it explains.

import type { ReactNode } from "react";

export function PageHeader({
  title,
  description,
  meta,
  actions,
}: {
  title: ReactNode;
  description?: ReactNode;
  /** Status badges or breadcrumb-like context rendered under the title. */
  meta?: ReactNode;
  /** Trailing controls, right-aligned on wide viewports. */
  actions?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div>
        <h1 className="page-title">{title}</h1>
        {meta && <div className="row page-meta">{meta}</div>}
        {description && <p className="page-desc">{description}</p>}
      </div>
      {actions && <div className="page-header-actions">{actions}</div>}
    </header>
  );
}
