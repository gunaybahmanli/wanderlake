// Neutral placeholder for a city image (no photos yet): a soft gradient with the city name.
// Decorative, so hidden from assistive technology; the page heading names the city.
export function CityImage({ name, size = "card" }: { name: string; size?: "card" | "hero" }) {
  return (
    <div className={`city-image city-image-${size}`} aria-hidden="true">
      <span>{name}</span>
    </div>
  );
}
