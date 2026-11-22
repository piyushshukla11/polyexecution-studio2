export type CollaborationMode = 'standalone' | 'connecting' | 'connected' | 'disconnected';

export interface CollaboratorUser {
  id: string;
  name: string;
  color: string;
  isSelf: boolean;
}

export interface CollaborationState {
  mode: CollaborationMode;
  roomId: string;
  activeUsers: CollaboratorUser[];
  backendAvailable: boolean;
  missingCapabilityNotice: string;
}
