// PIXINSIGHT-DEPENDENT — validate against the installed PixInsight/WBPP.
//
// Bundled PJSR (PixInsight JavaScript Runtime, ECMAScript-5) runner that stacks
// a keep-list of light frames into an integrated master via
// WeightedBatchPreprocessing (WBPP). It is invoked by seestar_refine.wbpp with:
//
//     PixInsight -r=<this script> --automation-mode --force-exit -a=<params.json>
//
// This file is SHIPPED as a data file (it lives inside the package dir so hatch
// includes it in the wheel) but is NEVER executed in CI — the Python side mocks
// the subprocess. It exists so a real PixInsight install can run it, and it must
// be validated/adjusted against the user's installed PixInsight + WBPP version.
//
// The params JSON (written by run_wbpp) has the shape:
//   {
//     "target":     "M27",
//     "lights":     ["C:/.../sub1.fit", ...],   // absolute keep-list paths
//     "output_dir": "C:/.../data/refine",
//     "register":   true,
//     "integrate":  true,
//     "rejection":  "kappa-sigma",
//     "alignment":  "auto"
//   }

/* global jsArguments, File, WeightedBatchPreprocessing, console */

function readParamsPath() {
   // The Python side passes "-a=<params.json>"; PixInsight exposes trailing
   // arguments to the script via the global jsArguments array.
   if (typeof jsArguments != "undefined" && jsArguments.length > 0) {
      for (var i = 0; i < jsArguments.length; ++i) {
         var arg = jsArguments[i];
         if (arg.indexOf("-a=") === 0)
            return arg.substring(3);
         if (arg.indexOf(".json") >= 0)
            return arg;
      }
   }
   throw new Error("wbpp_runner: no params JSON path in jsArguments");
}

function readParams(path) {
   var f = new File;
   f.openForReading(path);
   var bytes = f.read(DataType_ByteArray, f.size);
   f.close();
   return JSON.parse(bytes.toString());
}

function runWbpp(params) {
   // PIXINSIGHT-DEPENDENT: the WBPP scripting API differs across PixInsight
   // versions. This is the documented shape; confirm on the installed WBPP.
   var wbpp = new WeightedBatchPreprocessing;

   // Add the keep-list lights (pre-calibrated OSC subs → no darks/flats/bias).
   for (var i = 0; i < params.lights.length; ++i)
      wbpp.addLight(params.lights[i]);

   wbpp.outputDirectory = params.output_dir;
   wbpp.calibrate = false;               // Seestar subs are pre-calibrated
   wbpp.registerImages = params.register;
   wbpp.integrate = params.integrate;
   wbpp.generateRejectionMaps = false;

   wbpp.run();
}

function main() {
   var paramsPath = readParamsPath();
   var params = readParams(paramsPath);
   console.writeln("wbpp_runner: target=" + params.target +
                   " lights=" + params.lights.length +
                   " out=" + params.output_dir);
   runWbpp(params);
   console.writeln("wbpp_runner: done");
}

main();
