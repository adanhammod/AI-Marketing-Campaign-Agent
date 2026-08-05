import { QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Route, Routes } from 'react-router-dom'

import { createQueryClient } from './api/queryClient'
import { CampaignDetailPage } from './routes/CampaignDetailPage'
import { CreateCampaignPage } from './routes/CreateCampaignPage'

const queryClient = createQueryClient()

function Home() {
  return (
    <main>
      <h1>AI Marketing Campaign Agent</h1>
    </main>
  )
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/campaigns/new" element={<CreateCampaignPage />} />
          <Route path="/campaigns/:campaignId" element={<CampaignDetailPage />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}

export default App
