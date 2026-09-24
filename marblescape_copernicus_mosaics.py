"""Public Copernicus mosaic collections used by the Browser."""

QUARTERLY = "byoc-5460de54-082e-473a-b6ea-d5cbe3c17cca"
QUARTERLY_LOW = "byoc-6af2d932-8f18-4bed-a31b-d32bc49d43a0"
WORLDCOVER_ANNUAL = "byoc-65d4af89-5ce5-468e-bbbe-8a2fd9efaccc"
S1_DH_MONTHLY = "byoc-cc676fec-cb8d-4bc1-adce-1d9658da950b"
S1_DH_MONTHLY_LOW = "byoc-dd2837a1-8b59-46cf-ac58-3df2a3559803"
S1_IW_MONTHLY = "byoc-3c662330-108b-4378-8899-525fd5a225cb"
S1_IW_MONTHLY_LOW = "byoc-b4d63c63-7572-4277-8f4a-aa3d672820db"


def _optical_script(red, green, blue):
    return (
        '//VERSION=3\n'
        'function setup(){return {input:["B02","B03","B04","B08","dataMask"],'
        'output:{bands:4}};}\n'
        'var brightness = 1.0;\n'
        'var maxR=3.0, midR=0.13, saturation=1.2, gamma=1.8;\n'
        'var offset=0.01, offsetPow=Math.pow(offset,gamma);\n'
        'var offsetRange=Math.pow(1+offset,gamma)-offsetPow;\n'
        'function clip(v){return Math.max(0,Math.min(1,v));}\n'
        'function compress(v){'
        'var a=clip(v/maxR),t=midR/maxR;'
        'return a*(a*(t)-1)/(a*(2*t-1)-t);}\n'
        'function adjust(v){var a=compress(v*brightness);'
        'return (Math.pow(a+offset,gamma)-offsetPow)/offsetRange;}\n'
        'function srgb(v){return v<=0.0031308?12.92*v:1.055*Math.pow(v,1/2.4)-0.055;}\n'
        'function evaluatePixel(s){'
        f'var r=adjust({red}/10000),g=adjust({green}/10000),b=adjust({blue}/10000);'
        'var mean=(r+g+b)/3*(1-saturation);'
        'return [srgb(clip(mean+r*saturation)),'
        'srgb(clip(mean+g*saturation)),'
        'srgb(clip(mean+b*saturation)),s.dataMask];}'
    )


def _radar_ratio_script(co, cross, gain):
    return (
        '//VERSION=3\n'
        f'function setup(){{return {{input:["{co}","{cross}","dataMask"],'
        'output:{bands:4}};}\n'
        'var brightness = 1.0;\n'
        'var viz=new HighlightCompressVisualizer(0,0.8);\n'
        'function evaluatePixel(s){'
        f'var a=s.{co},b=s.{cross};'
        f'var gain={gain}*brightness;'
        'var rgb=viz.processList([gain*a/0.28,gain*b/0.06,'
        'gain*b/Math.max(a,0.000001)/0.49]);'
        'return [rgb[0],rgb[1],rgb[2],s.dataMask];}'
    )


def _radar_grey_script(co):
    return (
        '//VERSION=3\n'
        f'function setup(){{return {{input:["{co}","dataMask"],output:{{bands:4}}}};}}\n'
        'var brightness = 1.0;\n'
        f'function evaluatePixel(s){{var v=Math.min(1,Math.max(0,s.{co}*brightness/0.3));'
        'return [v,v,v,s.dataMask];}'
    )


def evalscript_for_brightness(layer, percent):
    script = layer["evalscript"]
    if "date_granularity" not in layer:
        return script
    return script.replace("var brightness = 1.0;", f"var brightness = {percent / 100:.2f};", 1)


def _layer(identifier, name, data_type, start, script, *, low_type=None,
           minimum_zoom=2, granularity="month"):
    value = {
        "id": identifier,
        "name": name,
        "style": "default",
        "data_type": data_type,
        "start_date": start,
        "data_filter": {},
        "processing": {"upsampling": "BILINEAR", "downsampling": "BILINEAR"},
        "evalscript": script,
        "minimum_zoom": minimum_zoom,
        "maximum_zoom": 25,
        "date_granularity": granularity,
    }
    if low_type:
        value["low_resolution_data_type"] = low_type
        value["low_resolution_threshold_m"] = 320
    return value


def _product(identifier, name, mission, layers):
    return {
        "id": "MARBLESCAPE::" + identifier,
        "instance": "MARBLESCAPE",
        "name": name,
        "missions": [mission],
        "layers": layers,
    }


MOSAIC_PRODUCTS = [
    _product("S2-QUARTERLY", "Sentinel-2 Quarterly Mosaics", "Sentinel-2 Mosaics", [
        _layer("TRUE_COLOR_CLOUDLESS", "True Color Cloudless", QUARTERLY,
               "2015-07-01", _optical_script("s.B04", "s.B03", "s.B02"),
               low_type=QUARTERLY_LOW, granularity="quarter"),
        _layer("FALSE_COLOR_CLOUDLESS", "False Color Cloudless", QUARTERLY,
               "2015-07-01", _optical_script("s.B08", "s.B04", "s.B03"),
               low_type=QUARTERLY_LOW, granularity="quarter"),
    ]),
    _product("S2-WORLDCOVER-ANNUAL", "WorldCover Annual Cloudless Mosaics",
             "Sentinel-2 Mosaics", [
        _layer("TRUE_COLOR_CLOUDLESS", "True Color Cloudless", WORLDCOVER_ANNUAL,
               "2020-01-01", _optical_script("s.B04", "s.B03", "s.B02"),
               minimum_zoom=9, granularity="year"),
        _layer("FALSE_COLOR_CLOUDLESS", "False Color Cloudless", WORLDCOVER_ANNUAL,
               "2020-01-01", _optical_script("s.B08", "s.B04", "s.B03"),
               minimum_zoom=9, granularity="year"),
    ]),
    _product("S1-DH-MONTHLY", "Sentinel-1 DH Monthly Mosaics", "Sentinel-1 Mosaics", [
        _layer("RGB_RATIO", "RGB Ratio", S1_DH_MONTHLY, "2014-10-01",
               _radar_ratio_script("HH", "HV", 0.8), low_type=S1_DH_MONTHLY_LOW),
        _layer("HH_GREY", "HH - grayscale", S1_DH_MONTHLY, "2014-10-01",
               _radar_grey_script("HH"), low_type=S1_DH_MONTHLY_LOW),
    ]),
    _product("S1-IW-MONTHLY", "Sentinel-1 IW Monthly Mosaics", "Sentinel-1 Mosaics", [
        _layer("RGB_RATIO", "RGB Ratio", S1_IW_MONTHLY, "2014-10-01",
               _radar_ratio_script("VV", "VH", 0.5), low_type=S1_IW_MONTHLY_LOW),
        _layer("VV_GREY", "VV - grayscale", S1_IW_MONTHLY, "2014-10-01",
               _radar_grey_script("VV"), low_type=S1_IW_MONTHLY_LOW),
    ]),
]
