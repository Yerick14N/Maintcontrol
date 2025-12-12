
import java.security.SecureRandom;
import java.time.Instant;

public class LicenseKeyGenerator {

    private static final SecureRandom RANDOM = new SecureRandom();

    public static String generate() {
        byte[] bytes = new byte[8];
        RANDOM.nextBytes(bytes);
        StringBuilder sb = new StringBuilder("MC-");
        for (byte b : bytes) {
            sb.append(String.format("%02X", b));
        }
        return sb.toString();
    }

    public static void main(String[] args) {
        System.out.println("Generated key at " + Instant.now() + ": " + generate());
    }
}
