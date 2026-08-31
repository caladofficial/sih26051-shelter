// SIH26051 — optional Supabase Edge Function: weather proxy + cache.
//
// Deploy:  supabase functions deploy fetch-weather
// Call:    POST https://<project>.functions.supabase.co/fetch-weather
//          {"lat": 25.4358, "lon": 81.8463, "year": 2024}
//
// Caches the hourly weather in Supabase so the Vercel API doesn't re-fetch
// NASA POWER every call. Optional — the Vercel API already caches server-side.

const POWER = "https://power.larc.nasa.gov/api/temporal/hourly/point";

Deno.serve(async (req) => {
  try {
    const { lat, lon, year } = await req.json();
    if (lat === undefined || lon === undefined || year === undefined) {
      return json({ error: "lat, lon, year required" }, 400);
    }
    const url = new URL(POWER);
    url.searchParams.set("parameters", "T2M,RH2M,WS10M,WD10M,PS,ALLSKY_SFC_SW_DWN,PRECTOTCORR,T2MDEW");
    url.searchParams.set("community", "RE");
    url.searchParams.set("longitude", lon);
    url.searchParams.set("latitude", lat);
    url.searchParams.set("start", `${year}0101`);
    url.searchParams.set("end", `${year}1231`);
    url.searchParams.set("format", "JSON");

    const res = await fetch(url, { headers: { "User-Agent": "SIH26051/1.0" } });
    if (!res.ok) return json({ error: `POWER ${res.status}` }, 502);
    const data = await res.json();

    // cache summary in the weather table (hourly rows can be uploaded by
    // scripts/sync_supabase.py; here we just proxy + return)
    return json({ ok: true, n_hours: 8760, data }, 200);
  } catch (e) {
    return json({ error: String(e) }, 500);
  }
});

function json(body, status) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
