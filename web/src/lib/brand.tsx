import brand from "@brand/brand.json";

// logo.svg in the brand folder is optional; without it the brand's `mark` letters are shown.
const logos = import.meta.glob("@brand/logo.svg", { eager: true, query: "?url", import: "default" });
const logoUrl = Object.values(logos)[0] as string | undefined;

export { brand };

export function BrandMark({ className, size }: { className: string; size: number }) {
  if (logoUrl) return <img src={logoUrl} alt="" width={size} height={size} style={{ display: "block", flexShrink: 0 }} />;
  return <div className={className}>{brand.mark}</div>;
}
