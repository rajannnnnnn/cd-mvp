import { useQuery } from '@tanstack/react-query'
import { api, ok } from '@/api/client'

export const rupees = (paise: number) => '₹' + (paise / 100).toLocaleString('en-IN', { minimumFractionDigits: paise % 100 ? 2 : 0, maximumFractionDigits: 2 })
export const useBilling = (enabled = true) => useQuery({ enabled, queryKey: ['billing'], queryFn: () => ok(api.GET('/api/v1/billing')), staleTime: 60_000 })
