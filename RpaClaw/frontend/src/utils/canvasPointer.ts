export interface BoxRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

export interface CanvasImageSize {
  width: number;
  height: number;
}

export interface PointerPosition {
  clientX: number;
  clientY: number;
}

export interface ImagePointerPosition {
  x: number;
  y: number;
  withinImage: boolean;
}

export function mapPointerToImageSpace(
  rect: BoxRect,
  pointer: PointerPosition,
  image: CanvasImageSize,
): ImagePointerPosition {
  if (rect.width <= 0 || rect.height <= 0 || image.width <= 0 || image.height <= 0) {
    return { x: 0, y: 0, withinImage: false };
  }

  const rectAspect = rect.width / rect.height;
  const imageAspect = image.width / image.height;

  let renderedWidth = rect.width;
  let renderedHeight = rect.height;
  let offsetLeft = 0;
  let offsetTop = 0;

  if (imageAspect > rectAspect) {
    renderedHeight = rect.width / imageAspect;
    offsetTop = (rect.height - renderedHeight) / 2;
  } else {
    renderedWidth = rect.height * imageAspect;
    offsetLeft = (rect.width - renderedWidth) / 2;
  }

  const xInImage = pointer.clientX - rect.left - offsetLeft;
  const yInImage = pointer.clientY - rect.top - offsetTop;
  const withinImage =
    xInImage >= 0 &&
    yInImage >= 0 &&
    xInImage <= renderedWidth &&
    yInImage <= renderedHeight;

  const x = Math.min(1, Math.max(0, xInImage / renderedWidth));
  const y = Math.min(1, Math.max(0, yInImage / renderedHeight));

  return { x, y, withinImage };
}
