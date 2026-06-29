/**
 * Top-level route table.
 *
 * Every authenticated route is nested under `<Layout>` so the topbar /
 * sidebar render once and content swaps via `<Outlet />`. Route
 * components are placeholders the follow-on tasks (T11–T14) will fill
 * in — keep them deliberately thin.
 */

import { createBrowserRouter, Navigate } from "react-router-dom";

import { Layout } from "@/components/Layout";
import { CaseDetailPage, CasesPage } from "@/pages/CasesPage";
import { ClipPage } from "@/pages/ClipPage";
import { EventTimelinePage } from "@/pages/EventTimelinePage";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { SearchPage } from "@/pages/SearchPage";
import { TrucksPage } from "@/pages/TrucksPage";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <Layout />,
    children: [
      { index: true, element: <Navigate to="/search" replace /> },
      { path: "search", element: <SearchPage /> },
      { path: "trucks", element: <TrucksPage /> },
      {
        path: "trucks/:id/events",
        element: <EventTimelinePage scope="truck" />,
      },
      {
        path: "drivers/:id/events",
        element: <EventTimelinePage scope="driver" />,
      },
      { path: "clips/:id", element: <ClipPage /> },
      { path: "cases", element: <CasesPage /> },
      { path: "cases/:id", element: <CaseDetailPage /> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
]);
