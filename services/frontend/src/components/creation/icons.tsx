import type { ReactNode } from 'react'

interface IconProps {
  className?: string
}

function Icon({ className, children }: IconProps & { children: ReactNode }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  )
}

// -- Platform icons (generic pictograms, not brand logos) --

export function InstagramIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="3.5" y="3.5" width="17" height="17" rx="5" />
      <circle cx="12" cy="12" r="4" />
      <circle cx="17" cy="7" r="0.5" fill="currentColor" />
    </Icon>
  )
}

export function TikTokIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M13 3v11.5a3.5 3.5 0 1 1-3-3.46" />
      <path d="M13 3c.6 2.6 2.4 4.3 5 4.7" />
    </Icon>
  )
}

export function FacebookIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="9" cy="9" r="3.5" />
      <circle cx="16" cy="10.5" r="2.75" />
      <path d="M4 19c.6-3 2.4-4.5 5-4.5s4.4 1.5 5 4.5" />
      <path d="M14.5 19c.4-2.3 1.8-3.6 4-3.9" />
    </Icon>
  )
}

export function YouTubeIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="3.5" y="5.5" width="17" height="13" rx="4" />
      <path d="M10.5 9.5v5l4.5-2.5-4.5-2.5Z" fill="currentColor" stroke="none" />
    </Icon>
  )
}

export function LinkedInIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="3.5" y="7" width="17" height="12.5" rx="2.5" />
      <path d="M3.5 7 12 13l8.5-6" />
    </Icon>
  )
}

// -- AI output preview icons --

export function StrategyIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="12" cy="12" r="8" />
      <path d="m14.5 9.5-2 4.5-4.5 2 2-4.5 4.5-2Z" />
    </Icon>
  )
}

export function CopyIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M5 4.5h11l3 3V19a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5.5a1 1 0 0 1 1-1Z" />
      <path d="M8 10h8M8 13.5h8M8 17h5" />
    </Icon>
  )
}

export function StoryboardIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="3.5" y="6" width="6" height="5" rx="1" />
      <rect x="14.5" y="6" width="6" height="5" rx="1" />
      <rect x="9" y="14" width="6" height="5" rx="1" />
      <path d="M9.5 8.5h5M12 11v3" />
    </Icon>
  )
}

export function ImagesIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="3.5" y="4.5" width="14" height="14" rx="2" />
      <circle cx="8.5" cy="9.5" r="1.5" />
      <path d="M3.5 15.5 8 12l3 2.5 3.5-3.5 3 3" />
      <path d="M20.5 8v9a2 2 0 0 1-2 2H8" />
    </Icon>
  )
}

export function VoiceoverIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="9.5" y="3.5" width="5" height="9" rx="2.5" />
      <path d="M6 11a6 6 0 0 0 12 0M12 17v3.5M9 20.5h6" />
    </Icon>
  )
}

export function VideoIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="3.5" y="7.5" width="12" height="10" rx="2" />
      <path d="m20.5 10-5 2.5 5 2.5V10Z" />
      <path d="m6.5 7.5 1.5-3h3l-1.5 3M12 7.5l1.5-3h2l-1.5 3" />
    </Icon>
  )
}

export function ArrowRightIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4.5 12h15M13.5 6l6 6-6 6" />
    </Icon>
  )
}

export function BuildingIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M6 20V5.5a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1V20" />
      <path d="M3 20h18M9.5 8h1.5M13 8h1.5M9.5 12h1.5M13 12h1.5M10 20v-4h4v4" />
    </Icon>
  )
}

export function MegaphoneIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4 10v4a1 1 0 0 0 1 1h2l9 4V5l-9 4H5a1 1 0 0 0-1 1Z" />
      <path d="M17.5 10.5a3 3 0 0 1 0 3M20 8.5a6 6 0 0 1 0 7" />
      <path d="M7 15v4a1.5 1.5 0 0 0 3 0v-3.5" />
    </Icon>
  )
}

export function UsersIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="9" cy="8.5" r="3" />
      <path d="M3.5 19c.6-3.3 2.7-5 5.5-5s4.9 1.7 5.5 5" />
      <path d="M15.5 6a3 3 0 0 1 0 5.8M19 19c-.4-2.3-1.5-3.9-3.3-4.7" />
    </Icon>
  )
}

export function CheckIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4.5 12.5 9.5 17.5 19.5 6.5" />
    </Icon>
  )
}

export function AlertIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 8v5" />
      <circle cx="12" cy="16" r="0.5" fill="currentColor" />
    </Icon>
  )
}

export function SparkleIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M12 3.5c.5 3 2 4.5 5 5-3 .5-4.5 2-5 5-.5-3-2-4.5-5-5 3-.5 4.5-2 5-5Z" />
      <path d="M19 14c.25 1.4.9 2.1 2.3 2.4-1.4.25-2.05.9-2.3 2.3-.25-1.4-.9-2.05-2.3-2.3 1.4-.3 2.05-1 2.3-2.4Z" />
    </Icon>
  )
}

export function SpinnerIcon(props: IconProps) {
  return (
    <svg
      className={props.className}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
      focusable="false"
    >
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="2.5" opacity="0.25" />
      <path
        d="M21 12a9 9 0 0 0-9-9"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
      />
    </svg>
  )
}
