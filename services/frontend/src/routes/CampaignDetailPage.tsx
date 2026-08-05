import { useParams } from 'react-router-dom'

export function CampaignDetailPage() {
  const { campaignId } = useParams<{ campaignId: string }>()

  return (
    <main>
      <h1>Campaign {campaignId}</h1>
    </main>
  )
}
