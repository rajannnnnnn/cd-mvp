import { useQuery } from '@tanstack/react-query'
import { api, ok } from './client'

export const usePublicConfig = () => useQuery({ queryKey: ['public-config'], queryFn: () => ok(api.GET('/api/v1/public/config')), staleTime: Infinity })
export const useBusiness = (enabled = true) => useQuery({ enabled, queryKey: ['business'], queryFn: () => ok(api.GET('/api/v1/business')) })
export const useOverview = (days = 14, enabled = true) => useQuery({ enabled, queryKey: ['metrics', 'overview', days], queryFn: () => ok(api.GET('/api/v1/metrics/overview', { params: { query: { days } } })), refetchInterval: 60_000 })
