import com.sun.jdi.AbsentInformationException;
import com.sun.jdi.ArrayReference;
import com.sun.jdi.BooleanValue;
import com.sun.jdi.ByteValue;
import com.sun.jdi.CharValue;
import com.sun.jdi.ClassType;
import com.sun.jdi.DoubleValue;
import com.sun.jdi.Field;
import com.sun.jdi.FloatValue;
import com.sun.jdi.IntegerValue;
import com.sun.jdi.LocalVariable;
import com.sun.jdi.Location;
import com.sun.jdi.Method;
import com.sun.jdi.LongValue;
import com.sun.jdi.ObjectReference;
import com.sun.jdi.ReferenceType;
import com.sun.jdi.ShortValue;
import com.sun.jdi.StackFrame;
import com.sun.jdi.StringReference;
import com.sun.jdi.ThreadReference;
import com.sun.jdi.Value;
import com.sun.jdi.VirtualMachine;
import com.sun.jdi.VMDisconnectedException;
import com.sun.jdi.connect.AttachingConnector;
import com.sun.jdi.connect.Connector;
import com.sun.jdi.event.BreakpointEvent;
import com.sun.jdi.event.ClassPrepareEvent;
import com.sun.jdi.event.Event;
import com.sun.jdi.event.EventQueue;
import com.sun.jdi.event.EventSet;
import com.sun.jdi.event.StepEvent;
import com.sun.jdi.request.ClassPrepareRequest;
import com.sun.jdi.request.BreakpointRequest;
import com.sun.jdi.request.EventRequest;
import com.sun.jdi.request.EventRequestManager;
import com.sun.jdi.request.StepRequest;
import com.sun.jdi.Bootstrap;
import java.util.ArrayList;
import java.util.Collections;
import java.util.IdentityHashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

public class DebugClient {
    public static void main(String[] args) throws Exception {
        int port = Integer.parseInt(args[0]);
        String className = args[1];

        VirtualMachine vm = attach(port);
        EventRequestManager erm = vm.eventRequestManager();
        ClassPrepareRequest cpr = erm.createClassPrepareRequest();
        cpr.addClassFilter(className);
        cpr.setSuspendPolicy(EventRequest.SUSPEND_ALL);
        cpr.enable();

        EventQueue queue = vm.eventQueue();
        List<Map<String, Object>> trace = new ArrayList<>();
        Set<String> seen = Collections.newSetFromMap(new LinkedHashMap<String, Boolean>());

        vm.resume();
        boolean done = false;
        while (!done) {
            try {
                EventSet set = queue.remove();
                for (Event event : set) {
                    if (event instanceof ClassPrepareEvent) {
                        installMainBreakpoint(erm, (ClassPrepareEvent) event);
                    } else if (event instanceof BreakpointEvent) {
                        BreakpointEvent breakpoint = (BreakpointEvent) event;
                        recordStep(trace, seen, breakpoint.thread().frame(0));
                        erm.deleteEventRequests(erm.breakpointRequests());
                        installStep(erm, breakpoint.thread(), className);
                    } else if (event instanceof StepEvent) {
                        StepEvent step = (StepEvent) event;
                        StackFrame frame = step.thread().frame(0);
                        recordStep(trace, seen, frame);
                        erm.deleteEventRequests(erm.stepRequests());
                        installStep(erm, step.thread(), className);
                    }
                }
                set.resume();
            } catch (VMDisconnectedException disconnected) {
                done = true;
            }
        }
        System.out.println(toJson(trace));
    }

    private static VirtualMachine attach(int port) throws Exception {
        for (AttachingConnector connector : Bootstrap.virtualMachineManager().attachingConnectors()) {
            if (!connector.name().equals("com.sun.jdi.SocketAttach")) {
                continue;
            }
            Map<String, Connector.Argument> args = connector.defaultArguments();
            args.get("hostname").setValue("127.0.0.1");
            args.get("port").setValue(Integer.toString(port));
            return connector.attach(args);
        }
        throw new IllegalStateException("SocketAttach connector is unavailable");
    }

    private static void installStep(EventRequestManager erm, ThreadReference thread, String className) {
        erm.deleteEventRequests(erm.stepRequests());
        StepRequest request = erm.createStepRequest(thread, StepRequest.STEP_LINE, StepRequest.STEP_OVER);
        request.addClassFilter(className);
        request.setSuspendPolicy(EventRequest.SUSPEND_ALL);
        request.enable();
    }

    private static void installMainBreakpoint(EventRequestManager erm, ClassPrepareEvent event) throws Exception {
        for (Method method : event.referenceType().methodsByName("main")) {
            if (!method.isStatic()) {
                continue;
            }
            Location location = method.location();
            BreakpointRequest request = erm.createBreakpointRequest(location);
            request.setSuspendPolicy(EventRequest.SUSPEND_ALL);
            request.enable();
            return;
        }
        throw new IllegalStateException("main method not found");
    }

    private static void recordStep(List<Map<String, Object>> trace, Set<String> seen, StackFrame frame) {
        int line = frame.location().lineNumber();
        Map<String, Object> variables = extractLocals(frame);
        String key = line + ":" + toJson(variables);
        if (seen.add(key)) {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("line", line);
            row.put("variables", variables);
            trace.add(row);
        }
    }

    private static Map<String, Object> extractLocals(StackFrame frame) {
        Map<String, Object> result = new LinkedHashMap<>();
        try {
            for (LocalVariable variable : frame.visibleVariables()) {
                result.put(variable.name(), valueOf(frame.getValue(variable), 0, newSeen()));
            }
        } catch (AbsentInformationException ignored) {
        }
        return result;
    }

    private static Set<ObjectReference> newSeen() {
        return Collections.newSetFromMap(new IdentityHashMap<ObjectReference, Boolean>());
    }

    private static Object valueOf(Value value, int depth, Set<ObjectReference> seen) {
        if (value == null) {
            return null;
        }
        if (depth > 5) {
            return "...";
        }
        if (value instanceof BooleanValue) {
            return ((BooleanValue) value).value();
        }
        if (value instanceof ByteValue) {
            return ((ByteValue) value).value();
        }
        if (value instanceof CharValue) {
            return Character.toString(((CharValue) value).value());
        }
        if (value instanceof ShortValue) {
            return ((ShortValue) value).value();
        }
        if (value instanceof IntegerValue) {
            return ((IntegerValue) value).value();
        }
        if (value instanceof LongValue) {
            return ((LongValue) value).value();
        }
        if (value instanceof FloatValue) {
            return ((FloatValue) value).value();
        }
        if (value instanceof DoubleValue) {
            return ((DoubleValue) value).value();
        }
        if (value instanceof StringReference) {
            return ((StringReference) value).value();
        }
        if (!(value instanceof ObjectReference)) {
            return value.toString();
        }

        ObjectReference object = (ObjectReference) value;
        if (!seen.add(object)) {
            return "...";
        }
        String typeName = object.referenceType().name();
        try {
            if (isBoxedPrimitive(typeName)) {
                return valueOf(object.getValue(field(object.referenceType(), "value")), depth + 1, seen);
            }
            if (object instanceof ArrayReference) {
                return arrayValues((ArrayReference) object, depth, seen);
            }
            if (isListLike(typeName)) {
                return arrayBackedValues(object, depth, seen);
            }
            if (typeName.equals("java.util.LinkedList")) {
                return linkedListValues(object, depth, seen);
            }
            if (typeName.equals("java.util.ArrayDeque")) {
                return arrayDequeValues(object, depth, seen);
            }
            if (isHashMap(typeName)) {
                return hashMapValues(object, depth, seen);
            }
            if (typeName.equals("java.util.TreeMap")) {
                Map<String, Object> map = new LinkedHashMap<>();
                treeMapValues((ObjectReference) object.getValue(field(object.referenceType(), "root")), map, depth, seen);
                return map;
            }
            if (isHashSet(typeName)) {
                Object map = valueOf(object.getValue(field(object.referenceType(), "map")), depth + 1, seen);
                return map instanceof Map ? new ArrayList<>(((Map<?, ?>) map).keySet()) : map;
            }
            if (typeName.equals("java.util.TreeSet")) {
                Object map = valueOf(object.getValue(field(object.referenceType(), "m")), depth + 1, seen);
                return map instanceof Map ? new ArrayList<>(((Map<?, ?>) map).keySet()) : map;
            }
        } finally {
            seen.remove(object);
        }
        return object.toString();
    }

    private static boolean isBoxedPrimitive(String name) {
        return name.equals("java.lang.Integer") || name.equals("java.lang.Long")
            || name.equals("java.lang.Boolean") || name.equals("java.lang.Byte")
            || name.equals("java.lang.Short") || name.equals("java.lang.Float")
            || name.equals("java.lang.Double") || name.equals("java.lang.Character");
    }

    private static boolean isListLike(String name) {
        return name.equals("java.util.ArrayList") || name.equals("java.util.Vector")
            || name.equals("java.util.Stack") || name.equals("java.util.PriorityQueue");
    }

    private static boolean isHashMap(String name) {
        return name.equals("java.util.HashMap") || name.equals("java.util.LinkedHashMap");
    }

    private static boolean isHashSet(String name) {
        return name.equals("java.util.HashSet") || name.equals("java.util.LinkedHashSet");
    }

    private static List<Object> arrayValues(ArrayReference array, int depth, Set<ObjectReference> seen) {
        List<Object> result = new ArrayList<>();
        for (Value item : array.getValues()) {
            result.add(valueOf(item, depth + 1, seen));
        }
        return result;
    }

    private static List<Object> arrayBackedValues(ObjectReference object, int depth, Set<ObjectReference> seen) {
        ReferenceType type = object.referenceType();
        String typeName = type.name();
        boolean priorityQueue = typeName.equals("java.util.PriorityQueue");
        String sizeField = typeName.equals("java.util.ArrayList") || priorityQueue ? "size" : "elementCount";
        int size = ((IntegerValue) object.getValue(field(type, sizeField))).value();
        String fieldName = priorityQueue ? "queue" : "elementData";
        ArrayReference elements = (ArrayReference) object.getValue(field(type, fieldName));
        List<Object> result = new ArrayList<>();
        for (int i = 0; i < size; i++) {
            result.add(valueOf(elements.getValue(i), depth + 1, seen));
        }
        return result;
    }

    private static List<Object> linkedListValues(ObjectReference object, int depth, Set<ObjectReference> seen) {
        List<Object> result = new ArrayList<>();
        ObjectReference node = (ObjectReference) object.getValue(field(object.referenceType(), "first"));
        while (node != null) {
            result.add(valueOf(node.getValue(field(node.referenceType(), "item")), depth + 1, seen));
            node = (ObjectReference) node.getValue(field(node.referenceType(), "next"));
        }
        return result;
    }

    private static List<Object> arrayDequeValues(ObjectReference object, int depth, Set<ObjectReference> seen) {
        ArrayReference elements = (ArrayReference) object.getValue(field(object.referenceType(), "elements"));
        int head = ((IntegerValue) object.getValue(field(object.referenceType(), "head"))).value();
        int tail = ((IntegerValue) object.getValue(field(object.referenceType(), "tail"))).value();
        List<Object> result = new ArrayList<>();
        for (int index = head; index != tail; index = (index + 1) % elements.length()) {
            result.add(valueOf(elements.getValue(index), depth + 1, seen));
        }
        return result;
    }

    private static Map<String, Object> hashMapValues(ObjectReference object, int depth, Set<ObjectReference> seen) {
        Map<String, Object> result = new LinkedHashMap<>();
        ArrayReference table = (ArrayReference) object.getValue(field(object.referenceType(), "table"));
        if (table == null) {
            return result;
        }
        for (Value bucket : table.getValues()) {
            ObjectReference node = (ObjectReference) bucket;
            while (node != null) {
                Object key = valueOf(node.getValue(field(node.referenceType(), "key")), depth + 1, seen);
                Object val = valueOf(node.getValue(field(node.referenceType(), "value")), depth + 1, seen);
                result.put(String.valueOf(key), val);
                node = (ObjectReference) node.getValue(field(node.referenceType(), "next"));
            }
        }
        return result;
    }

    private static void treeMapValues(ObjectReference node, Map<String, Object> result, int depth, Set<ObjectReference> seen) {
        if (node == null) {
            return;
        }
        treeMapValues((ObjectReference) node.getValue(field(node.referenceType(), "left")), result, depth, seen);
        Object key = valueOf(node.getValue(field(node.referenceType(), "key")), depth + 1, seen);
        Object val = valueOf(node.getValue(field(node.referenceType(), "value")), depth + 1, seen);
        result.put(String.valueOf(key), val);
        treeMapValues((ObjectReference) node.getValue(field(node.referenceType(), "right")), result, depth, seen);
    }

    private static Field field(ReferenceType type, String name) {
        ReferenceType current = type;
        while (current != null) {
            Field field = current.fieldByName(name);
            if (field != null) {
                return field;
            }
            current = current instanceof ClassType ? ((ClassType) current).superclass() : null;
        }
        throw new IllegalArgumentException("missing field " + name + " on " + type.name());
    }

    private static String toJson(Object value) {
        if (value == null) {
            return "null";
        }
        if (value instanceof String) {
            return "\"" + escape((String) value) + "\"";
        }
        if (value instanceof Number || value instanceof Boolean) {
            return value.toString();
        }
        if (value instanceof Map) {
            StringBuilder out = new StringBuilder("{");
            boolean first = true;
            for (Object entryObj : ((Map<?, ?>) value).entrySet()) {
                Map.Entry<?, ?> entry = (Map.Entry<?, ?>) entryObj;
                if (!first) {
                    out.append(",");
                }
                first = false;
                out.append(toJson(String.valueOf(entry.getKey()))).append(":").append(toJson(entry.getValue()));
            }
            return out.append("}").toString();
        }
        if (value instanceof Iterable) {
            StringBuilder out = new StringBuilder("[");
            boolean first = true;
            for (Object item : (Iterable<?>) value) {
                if (!first) {
                    out.append(",");
                }
                first = false;
                out.append(toJson(item));
            }
            return out.append("]").toString();
        }
        return toJson(value.toString());
    }

    private static String escape(String value) {
        StringBuilder out = new StringBuilder();
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            if (c == '"' || c == '\\') {
                out.append('\\').append(c);
            } else if (c == '\n') {
                out.append("\\n");
            } else if (c == '\r') {
                out.append("\\r");
            } else if (c == '\t') {
                out.append("\\t");
            } else {
                out.append(c);
            }
        }
        return out.toString();
    }
}
