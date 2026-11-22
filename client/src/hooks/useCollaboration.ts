import { useState } from 'react';
import { CollaborationState } from '../types/collaboration';

export const MISSING_BACKEND_COLLAB_NOTICE =
  'Real-time multi-user editing requires a persistent WebSocket CRDT document gateway (e.g., Yjs/Automerge daemon). ' +
  'The current PolyExecution Studio 2 backend provides ephemeral gRPC execution worker daemons. ' +
  'Operating in honest standalone editor mode without fake simulated peer cursors.';

export function useCollaboration(initialRoomId: string = 'pes2-default-room'): {
  state: CollaborationState;
  connectRoom: (roomId: string) => void;
  leaveRoom: () => void;
} {
  const [state, setState] = useState<CollaborationState>({
    mode: 'standalone',
    roomId: initialRoomId,
    activeUsers: [
      {
        id: 'operator-1',
        name: 'Local Operator',
        color: '#2563eb',
        isSelf: true,
      },
    ],
    backendAvailable: false,
    missingCapabilityNotice: MISSING_BACKEND_COLLAB_NOTICE,
  });

  const connectRoom = (roomId: string) => {
    // Truthfully report that multi-user sync server is not present
    setState((prev) => ({
      ...prev,
      roomId,
      mode: 'standalone',
      missingCapabilityNotice: MISSING_BACKEND_COLLAB_NOTICE,
    }));
  };

  const leaveRoom = () => {
    setState((prev) => ({
      ...prev,
      mode: 'standalone',
    }));
  };

  return { state, connectRoom, leaveRoom };
}
