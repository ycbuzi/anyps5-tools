import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Data;
import ghidra.program.model.listing.DataIterator;
import ghidra.program.model.listing.Function;
import ghidra.program.model.symbol.Reference;

public class AnyPS5Read extends GhidraScript {
    @Override
    public void run() throws Exception {
        println("PROGRAM format=" + currentProgram.getExecutableFormat() + " language=" + currentProgram.getLanguageID() + " base=" + currentProgram.getImageBase() + " functions=" + currentProgram.getFunctionManager().getFunctionCount());
        DecompInterface decompiler = new DecompInterface();
        decompiler.openProgram(currentProgram);
        for (String what : getScriptArgs()) {
            if (what.startsWith("0x")) {
                Address address = toAddr(Long.decode(what));
                Function function = getFunctionContaining(address);
                println("=== " + what + " in " + (function == null ? "no function" : function.getName() + " at " + function.getEntryPoint()));
                if (function == null) continue;
                DecompileResults results = decompiler.decompileFunction(function, 180, monitor);
                println(results.decompileCompleted() ? results.getDecompiledFunction().getC() : "could not decompile: " + results.getErrorMessage());
            } else {
                println("=== text " + what);
                int found = 0;
                DataIterator data = currentProgram.getListing().getDefinedData(true);
                while (data.hasNext() && found < 12) {
                    Data item = data.next();
                    if (!item.hasStringValue()) continue;
                    String text = String.valueOf(item.getValue());
                    if (!text.contains(what)) continue;
                    found++;
                    println(item.getAddress() + " " + text);
                    for (Reference reference : getReferencesTo(item.getAddress())) {
                        Function user = getFunctionContaining(reference.getFromAddress());
                        println("  used at " + reference.getFromAddress() + " in " + (user == null ? "no function" : user.getName()));
                    }
                }
                if (found == 0) println("not found");
            }
        }
        decompiler.dispose();
    }
}
