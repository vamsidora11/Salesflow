import { screen } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { renderApp } from "../test/utils";
import ProtectedRoute from "./ProtectedRoute";

const tree = (
  <Routes>
    <Route path="/login" element={<p>login page</p>} />
    <Route element={<ProtectedRoute />}>
      <Route path="/exports" element={<p>secret exports</p>} />
    </Route>
  </Routes>
);

describe("ProtectedRoute", () => {
  it("redirects unauthenticated users to /login", () => {
    renderApp(tree, { role: null, route: "/exports" });
    expect(screen.getByText("login page")).toBeInTheDocument();
    expect(screen.queryByText("secret exports")).not.toBeInTheDocument();
  });

  it("renders the child route when authenticated", () => {
    renderApp(tree, { role: "analyst", route: "/exports" });
    expect(screen.getByText("secret exports")).toBeInTheDocument();
  });
});
