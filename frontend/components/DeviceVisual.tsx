import Image from "next/image";
import type { DeviceType } from "@/lib/types";

const ASSETS: Record<DeviceType, string> = {
  router: "/device-assets/generic-router.svg",
  switch: "/device-assets/generic-switch.svg",
  "access-point": "/device-assets/generic-access-point.svg",
  desktop: "/device-assets/generic-desktop.svg",
  laptop: "/device-assets/generic-laptop.svg",
  server: "/device-assets/generic-server.svg",
  phone: "/device-assets/generic-phone.svg",
  "tv-media": "/device-assets/generic-tv.svg",
  printer: "/device-assets/generic-printer.svg",
  camera: "/device-assets/generic-camera.svg",
  unknown: "/device-assets/unknown-device.svg",
};

export function deviceAssetFor(type: DeviceType): string {
  return ASSETS[type] || ASSETS.unknown;
}

export default function DeviceVisual({
  type,
  label,
  expected = false,
  size = 74,
}: {
  type: DeviceType;
  label: string;
  expected?: boolean;
  size?: number;
}) {
  return (
    <span className={`device-visual ${expected ? "device-visual--expected" : ""}`}>
      <Image
        src={deviceAssetFor(type)}
        alt={`${label} visual`}
        width={size}
        height={Math.round(size * 0.67)}
        unoptimized
      />
    </span>
  );
}
