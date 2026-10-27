import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;
import java.io.File;
import java.io.PrintWriter;

public class ExportDecompiledCode extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        String outputPath = (args != null && args.length > 0) ? args[0] : "decompiled_output.c";
        File outFile = new File(outputPath);
        if (outFile.getParentFile() != null) {
            outFile.getParentFile().mkdirs();
        }
        
        DecompInterface decompiler = new DecompInterface();
        decompiler.openProgram(currentProgram);
        
        try (PrintWriter writer = new PrintWriter(outFile)) {
            writer.println("// Decompiled with Ghidra Headless Analyzer");
            writer.println("// Target: " + currentProgram.getName());
            writer.println();
            
            FunctionIterator functions = currentProgram.getFunctionManager().getFunctions(true);
            while (functions.hasNext() && !monitor.isCancelled()) {
                Function f = functions.next();
                writer.println("// Function: " + f.getName() + " @ " + f.getEntryPoint());
                DecompileResults results = decompiler.decompileFunction(f, 30, monitor);
                if (results.decompileCompleted() && results.getDecompiledFunction() != null) {
                    writer.println(results.getDecompiledFunction().getC());
                } else {
                    writer.println("// Failed to decompile " + f.getName() + ": " + results.getErrorMessage());
                }
                writer.println();
            }
        } finally {
            decompiler.dispose();
        }
        println("Decompilation successfully written to: " + outFile.getAbsolutePath());
    }
}
