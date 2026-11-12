import { Button, Card, formatDateTime } from '../components/ui'
import { useTalentAuth } from './TalentAuthContext'
import TalentMyPasswordPage from './TalentMyPasswordPage'

// The Talent Pool account: separate from the CRM's own My Profile.
export default function TalentProfilePage() {
  const { me, logout } = useTalentAuth()
  return (
    <div className="stack">
      <h1 className="page-title">My Profile</h1>
      <div className="narrow stack">
        <Card title="Talent Pool account">
          <dl className="details">
            <dt>Name</dt>
            <dd>{me.display_name}</dd>
            <dt>Username</dt>
            <dd className="mono">{me.username}</dd>
            <dt>Admin</dt>
            <dd>{me.admin_active ? `Unlocked until ${formatDateTime(me.admin_until)}` : 'Locked'}</dd>
          </dl>
          <p className="muted small">This is your Talent Pool login. It's separate from your CRM login.</p>
          <div>
            <Button variant="ghost" onClick={logout}>
              Log out of Talent Pool
            </Button>
          </div>
        </Card>
        <TalentMyPasswordPage />
      </div>
    </div>
  )
}
