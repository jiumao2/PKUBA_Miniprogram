interface ScoresheetLogoProps {
  className?: string;
  title?: string;
}

export function ScoresheetLogo({ className = '', title }: ScoresheetLogoProps) {
  return (
    <svg
      className={className}
      data-testid="scoresheet-logo"
      viewBox="0 0 32 40"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      role={title ? 'img' : undefined}
      aria-hidden={title ? undefined : true}
    >
      {title ? <title>{title}</title> : null}
      <rect
        x="3.75"
        y="2.75"
        width="24.5"
        height="34.5"
        rx="2"
        stroke="currentColor"
        strokeWidth="1.25"
      />
      <g stroke="currentColor" strokeWidth="1">
        <rect x="7" y="6.5" width="18" height="5" rx=".5" />
        <rect x="7" y="14" width="10.5" height="14" rx=".5" />
        <rect x="19" y="14" width="6" height="14" rx=".5" />
        <rect x="7" y="30.5" width="18" height="3.5" rx=".5" />
      </g>
    </svg>
  );
}
