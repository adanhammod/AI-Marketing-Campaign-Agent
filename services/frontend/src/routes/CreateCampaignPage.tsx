import { useNavigate } from 'react-router-dom'

import { CampaignForm } from '../components/creation/CampaignForm'

export function CreateCampaignPage() {
  const navigate = useNavigate()

  return (
    <main>
      <h1>Create campaign</h1>
      <CampaignForm onCreated={(campaignId) => navigate(`/campaigns/${campaignId}`)} />
    </main>
  )
}
