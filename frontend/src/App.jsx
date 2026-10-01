import { Navigate, RouterProvider, createBrowserRouter } from "react-router-dom";

import Layout from "./components/Layout";
import { AuthProvider, useAuth } from "./lib/AuthContext";
import Dashboard from "./pages/Dashboard";
import Entities from "./pages/Entities";
import EntityDetail from "./pages/EntityDetail";
import Import from "./pages/Import";
import Jobs from "./pages/Jobs";
import Login from "./pages/Login";
import Register from "./pages/Register";
import Search from "./pages/Search";

function RequireAuth({ children }) {
  const { status } = useAuth();
  if (status === "loading") {
    return (
      <div className="grid min-h-screen place-items-center text-sm text-slate-500">
        Checking session…
      </div>
    );
  }
  if (status !== "authed") return <Navigate to="/login" replace />;
  return children;
}

function PublicOnly({ children }) {
  const { status } = useAuth();
  if (status === "loading") {
    return (
      <div className="grid min-h-screen place-items-center text-sm text-slate-500">
        Checking session…
      </div>
    );
  }
  if (status === "authed") return <Navigate to="/" replace />;
  return children;
}

export default function App() {
  return (
    <AuthProvider>
      <RouterProvider router={router} />
    </AuthProvider>
  );
}

const router = createBrowserRouter([
  {
    path: "/login",
    element: (
      <PublicOnly>
        <Login />
      </PublicOnly>
    ),
  },
  {
    path: "/register",
    element: (
      <PublicOnly>
        <Register />
      </PublicOnly>
    ),
  },
  {
    path: "/",
    element: (
      <RequireAuth>
        <Layout />
      </RequireAuth>
    ),
    children: [
      { index: true, element: <Dashboard /> },
      { path: "dashboard", element: <Navigate to="/" replace /> },
      { path: "import", element: <Import /> },
      { path: "search", element: <Search /> },
      { path: "entities", element: <Entities /> },
      { path: "entities/:id", element: <EntityDetail /> },
      { path: "jobs", element: <Jobs /> },
      { path: "*", element: <Navigate to="/" replace /> },
    ],
  },
]);