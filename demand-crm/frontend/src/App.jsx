import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import AdminAiPage from './admin/AdminAiPage'
import AdminApprovalsPage from './admin/AdminApprovalsPage'
import AdminGate from './admin/AdminGate'
import AdminNameRequestsPage from './admin/AdminNameRequestsPage'
import AdminPasswordPage from './admin/AdminPasswordPage'
import AdminUsagePage from './admin/AdminUsagePage'
import AdminUsersPage from './admin/AdminUsersPage'
import { useAuth } from './auth/AuthContext'
import Layout from './components/Layout'
import AdvertisementsPage from './pages/AdvertisementsPage'
import ChatPage from './pages/ChatPage'
import DashboardPage from './pages/DashboardPage'
import DemandListPage from './pages/DemandListPage'
import LoginPage from './pages/LoginPage'
import MyProfilePage from './pages/MyProfilePage'
import TalentAdminGate from './talent/admin/TalentAdminGate'
import TalentAdminPasswordPage from './talent/admin/TalentAdminPasswordPage'
import TalentAdminSettingsPage from './talent/admin/TalentAdminSettingsPage'
import TalentAdminUsersPage from './talent/admin/TalentAdminUsersPage'
import TalentGate from './talent/TalentGate'
import TalentPoolPage from './talent/TalentPoolPage'
import TalentProfilePage from './talent/TalentProfilePage'
import TalentPipelinePage from './talent/TalentPipelinePage'

function RequireLogin({ children }) {
  const { me } = useAuth()
  const location = useLocation()
  if (me === undefined) return <div className="splash">Loading…</div>
  if (me === null) return <Navigate to="/login" replace state={{ from: location.pathname }} />
  return children
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <RequireLogin>
            <Layout />
          </RequireLogin>
        }
      >
        <Route index element={<Navigate to="/demands" replace />} />
        <Route path="/demands" element={<DemandListPage />} />
        <Route path="/demands/:demandId/chat" element={<ChatPage />} />
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/advertisements" element={<AdvertisementsPage />} />
        <Route path="/profile" element={<MyProfilePage />} />
        <Route path="/admin" element={<AdminGate />}>
          <Route index element={<Navigate to="/admin/approvals" replace />} />
          <Route path="approvals" element={<AdminApprovalsPage />} />
          <Route path="users" element={<AdminUsersPage />} />
          <Route path="name-requests" element={<AdminNameRequestsPage />} />
          <Route path="ai" element={<AdminAiPage />} />
          <Route path="usage" element={<AdminUsagePage />} />
          <Route path="password" element={<AdminPasswordPage />} />
        </Route>
        <Route path="/talent" element={<TalentGate />}>
          <Route index element={<TalentPoolPage />} />
          <Route path="profile" element={<TalentProfilePage />} />
          <Route path="password" element={<Navigate to="/talent/profile" replace />} />
          <Route path="pipeline" element={<TalentPipelinePage />} />
          <Route path="admin" element={<TalentAdminGate />}>
            <Route index element={<Navigate to="/talent/admin/users" replace />} />
            <Route path="users" element={<TalentAdminUsersPage />} />
            <Route path="settings" element={<TalentAdminSettingsPage />} />
            <Route path="password" element={<TalentAdminPasswordPage />} />
          </Route>
        </Route>
        <Route path="*" element={<Navigate to="/demands" replace />} />
      </Route>
    </Routes>
  )
}
