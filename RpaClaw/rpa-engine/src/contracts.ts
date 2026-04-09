export type RuntimeSession = {
  id: string;
  userId: string;
  mode: 'idle' | 'recording' | 'replaying' | 'stopped';
};
