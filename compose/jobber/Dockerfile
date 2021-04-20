FROM jobber@sha256:10e354645c84f98fcb5b3a49b578e47d3ad16406e94d763e6e1784397fef208c

USER root
RUN apk add httpie

COPY jobber.conf /home/jobberuser/.jobber
RUN chown jobberuser /home/jobberuser/.jobber
RUN chmod 600 /home/jobberuser/.jobber
USER jobberuser
