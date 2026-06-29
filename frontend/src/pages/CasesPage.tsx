/**
 * Re-exports the case list and detail pages so existing imports (the
 * router, tests) keep working without churning each call site.
 *
 * The implementations live in CaseListPage.tsx and CaseDetailPage.tsx —
 * one file per page so they don't crowd each other.
 */

export { CaseListPage as CasesPage } from "./CaseListPage";
export { CaseDetailPage } from "./CaseDetailPage";
