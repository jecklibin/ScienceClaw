import { ref } from 'vue'

import type { ScreencastSize } from '@/utils/screencastGeometry'

export function useRecorderScreencast() {
  const canvasRef = ref<HTMLCanvasElement | null>(null)
  const frameSize = ref<ScreencastSize>({ width: 1280, height: 720 })
  const inputSize = ref<ScreencastSize>({ width: 1280, height: 720 })
  const connected = ref(false)

  return {
    canvasRef,
    frameSize,
    inputSize,
    connected,
  }
}
